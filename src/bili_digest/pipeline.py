"""管线编排：URL 路由（B站 | 小红书）→ 字幕探测（含污染校验降级）→ 无字幕回退本地 ASR → Transcript。

主流程（方案设计 §3）：
URL → view 元信息（cid / 分P）→ 字幕轨探测（登录态）→ 有轨 → 下载 → validate
  → 通过 → Transcript(source=subtitle)
  → 无轨 / 校验失败（降级不报错）→ DASH 音频 + ffmpeg → ASR 引擎 → Transcript(source=asr)
小红书（S4）：分享链接 → __INITIAL_STATE__ 笔记解析 → mediaV2 内嵌字幕 → validate；
  无字幕 → 视频流下载 + ffmpeg → ASR。
"""

import json
import re
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from . import audio, enrich, validate
from .errors import CliError, log
from .platforms import bilibili, xiaohongshu
from .summarize import report
from .transcribe import CloudSfTranscriber, MlxTranscriber
from .transcribe.base import Segment, Transcript, Transcriber

ENGINES = {"mlx": MlxTranscriber, "cloud": CloudSfTranscriber}


@dataclass
class Result:
    transcript: Transcript
    meta: dict
    run_dir: Path | None = None  # --visual 时产物目录（frames/ 与 download/ 已落盘）


def pick_engine(name: str) -> Transcriber:
    """auto：本地 mlx 可用 → mlx；否则 cloud（无 key 时给配置指引）。D-010：云端是保底主路线。"""
    if name == "auto":
        name = "mlx" if ENGINES["mlx"]().available() else "cloud"
    if name == "mlx":
        eng = ENGINES["mlx"]()
        if not eng.available():
            raise CliError("本地引擎不可用（需 Apple Silicon 且安装 bili-digest[mlx]）；"
                           "可改用 --engine cloud（需 SILICONFLOW_API_KEY）", 3)
        return eng
    if name == "cloud":
        eng = ENGINES["cloud"]()
        if not eng.available():
            raise CliError("云引擎需配置 SILICONFLOW_API_KEY（~/.config/ai-tools/bili-digest/keys.env "
                           "或环境变量），运行 digest doctor 查看指引", 3)
        return eng
    raise CliError(f"未知引擎: {name}（支持 auto|mlx|cloud）", 2)


def segs_from_subtitle(raw) -> list[Segment]:
    return [Segment(start=s["from"], end=s["to"], text=s["text"]) for s in raw]


def note_suspected_silence(transcript: Transcript, meta: dict, visual=False) -> None:
    """ASR 疑似无语音（幻觉循环特征命中）：stderr 给指引 + 记入 meta 供总结 prompt 注明。
    只提示不改退出码——转写本身算成功。"""
    if not transcript.suspected_silent:
        return
    meta["suspected_silent"] = True
    hint = ("已启用 --visual，帧描述将成为报告主内容源" if visual
            else "可加 --visual 提取画面文字（帧描述会成为报告主内容源）；"
                 "当前未加 --visual，总结报告会注明转写疑似幻觉")
    log("转写疑似无语音（检测到幻觉循环特征）：该视频可能没有语音，内容可能在画面文字中；"
        + hint)


# ---------------------------------------------------------------- 视觉增强（S6 / S6.1 模式与门控）

LOW_DIVERSITY_RATIO = 0.8  # 本地预筛：≥80% 候选落同一簇 → 口播/访谈特征，跳过全部读图
PROBE_FRAMES = 3           # VLM 探测帧数（取首/中/尾，兼顾覆盖与成本 ≈¥0.002/次）


def _visual_run_dir(meta: dict, work_dir) -> Path:
    """--visual 的产物目录：外部指定优先，否则按 bvid/note_id 新建（复用 report 命名）。"""
    return Path(work_dir) if work_dir else report.new_run_dir(
        meta.get("bvid") or meta.get("note_id"))


def normalize_visual(visual) -> str | None:
    """visual 入参归一：True/「auto」→ "auto"；False/None → None（纯音频，不下载视频）；
    "ppt" 原样。非法值报用法错误。"""
    if visual in (None, False):
        return None
    if visual is True:
        return "auto"
    if isinstance(visual, str) and visual.strip().lower() in ("auto", "ppt"):
        return visual.strip().lower()
    raise CliError(f"visual 需要布尔值或 auto|ppt，收到: {visual!r}", 2)


def type_profile(vtype) -> dict:
    """--type 语义糖衣（S7，用户词汇 → 管线行为）：
    talk=内容在说话（不下载视频轨）｜slides=内容在画面（ppt 取帧 + 跳过 ASR，
    transcript 标 visual-only）｜mixed=两边都要（auto 取帧 + 禁用口播门控，双通道保证）
    ｜auto/None=现状默认行为。非法值 exit 2。"""
    profiles = {
        None: {"visual": None, "skip_asr": False, "no_gate": False},
        "auto": {"visual": None, "skip_asr": False, "no_gate": False},
        "talk": {"visual": None, "skip_asr": False, "no_gate": False},
        "slides": {"visual": "ppt", "skip_asr": True, "no_gate": False},
        "mixed": {"visual": "auto", "skip_asr": False, "no_gate": True},
    }
    if vtype not in profiles:
        raise CliError(f"未知类型: {vtype!r}（支持 talk|slides|mixed|auto）", 2)
    return profiles[vtype]


def _gate_meta(meta: dict, gate: str, note: str) -> None:
    """门控命中：不嵌帧、不读图，meta 记原因（报告据此诚实降级说明）。"""
    meta["frames"] = []
    meta["visual_gate"] = gate
    log(note)


CONTENT_FRAME_BUDGET = 80  # 过滤后内容帧上限（stage_talking 之外的保留配额，S6.4）


def filter_stage_frames(keyframes, descs, budget=CONTENT_FRAME_BUDGET):
    """读图后帧级过滤（S6.4，VLM 批量读图之后、报告注入之前；ppt 模式同样应用——
    设计裁定：15 页纯 PPT 无 stage 帧不受影响，混合 PPT+主播视频受益）：
    stage_talking 全弃（保留至多 1 张最早者当封面候选）；内容帧超 budget 时按静态优先
    抽稀。返回 (保留 (kf, desc) 对列表 按时间升序, 类型分布 dict)。"""
    stage = [(k, d) for k, d in zip(keyframes, descs)
             if d.get("frame_type") == "stage_talking"]
    content = [(k, d) for k, d in zip(keyframes, descs)
               if d.get("frame_type") != "stage_talking"]
    dist = {}
    for d in descs:
        ftype = d.get("frame_type") or "other"
        dist[ftype] = dist.get(ftype, 0) + 1
    if len(content) > budget:
        keep = enrich.frames.static_priority_thin([k for k, _ in content], budget)
        keep_ids = {id(k) for k in keep}
        content = [(k, d) for k, d in content if id(k) in keep_ids]
    kept = sorted([*content, *stage[:1]], key=lambda kd: kd[0].t)
    return kept, dist


def read_frames(meta: dict, run_dir: Path, video_path: Path, mode="auto",
                no_gate=False) -> None:
    """本地取帧（聚簇取末）+ 画面价值门控 + VLM 读图 → meta['frames'] / meta['vision']。
    门控（S6.1）：① 本地预筛——最大簇占比 ≥ 0.8（低视觉多样性，口播/访谈特征）跳过全部
    读图，meta 记 visual_gate="low_diversity"；② VLM 探测——auto 模式多样性正常时取 ≤3 帧
    问内容类型，口播型停止读图记 visual_gate="talking_head"。--visual=ppt 与 --type=mixed
    （no_gate，S7 双通道保证）不受门控影响（用户显式指定优先）。
    失败降级不炸 transcript（记 meta['visual_error']）。"""
    try:
        duration = meta.get("duration") or enrich.frames.probe_duration(video_path)
        keyframes, stats = enrich.frames.extract_keyframes(
            video_path, run_dir / "frames", duration=duration, mode=mode)
        meta.setdefault("timings", {}).update({  # 资源画像：ffmpeg 扫描 / 抽帧+聚簇
            "frames_scan_s": stats.get("scan_s"),
            "frames_grab_s": stats.get("grab_s"),
        })
        gated = not no_gate and mode != "ppt"  # ppt/mixed：用户显式要求双通道，跳过门控
        ratio = stats.get("largest_cluster_ratio")
        if gated and ratio is not None and ratio >= LOW_DIVERSITY_RATIO:
            _gate_meta(meta, "low_diversity",
                       f"画面价值门控命中（低视觉多样性：最大簇占比 {ratio}，"
                       "口播/访谈特征），跳过读图")
            return
        probe_usage = None
        if gated and len(keyframes) >= 2:
            probe_set = (keyframes if len(keyframes) <= PROBE_FRAMES else
                         [keyframes[0], keyframes[len(keyframes) // 2], keyframes[-1]])
            verdict, probe_usage = enrich.vlm.probe_content_type(
                probe_set, title=meta.get("title"), owner=meta.get("owner"))
            if verdict == "talking_head":
                _gate_meta(meta, "talking_head",
                           "画面价值门控命中（人物口播型），停止读图，不嵌帧")
                vconf = enrich.vlm.vision_conf()
                meta["vision"] = {
                    "provider": vconf["provider"], "model": vconf["model"], "frames": 0,
                    "prompt_tokens": probe_usage["prompt_tokens"],
                    "completion_tokens": probe_usage["completion_tokens"],
                    "total_tokens": probe_usage["prompt_tokens"] + probe_usage["completion_tokens"],
                    "cost_yuan": enrich.vlm.estimate_cost(
                        probe_usage["prompt_tokens"], probe_usage["completion_tokens"]),
                    "gated": "talking_head",
                }
                return
        if not keyframes:
            meta["frames"] = []
            log("未提取到关键帧，跳过画面识别")
            return
        descs, usage = enrich.vlm.describe_frames(
            keyframes, title=meta.get("title"), owner=meta.get("owner"))
        if probe_usage:  # 探测成本并入 vision 记账（判定为内容型后读图照常）
            usage["prompt_tokens"] += probe_usage["prompt_tokens"]
            usage["completion_tokens"] += probe_usage["completion_tokens"]
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            usage["cost_yuan"] = round(
                enrich.vlm.estimate_cost(usage["prompt_tokens"], usage["completion_tokens"]), 6)
        kept, type_dist = filter_stage_frames(keyframes, descs)  # S6.4 帧级信息过滤
        meta["frames"] = [
            {"id": kf.fid, "t": desc["t"], "file": f"frames/{kf.path.name}",
             "desc": desc["desc"], "has_text": bool(desc.get("has_text")),
             "type": desc.get("frame_type") or "other"}
            for kf, desc in kept]
        meta["vision"] = usage
        meta["frame_types"] = type_dist
        meta["thin_funnel"] = {  # S6.4 抽稀漏斗：candidates→clusters→after_thin→after_vlm→kept
            "candidates": stats.get("candidates"),
            "clusters": stats.get("clusters"),
            "after_thin": stats.get("after_thin"),
            "after_vlm": len(descs),
            "kept": len(kept),
        }
        meta.setdefault("timings", {})["vlm_s"] = usage.get("elapsed_s")
        log(f"画面识别 {len(meta['frames'])} 帧 · {usage['model']} · "
            f"{usage['total_tokens']} tokens · ¥{usage['cost_yuan']:.4f}")
    except CliError as err:
        meta["frames"] = []
        try:  # S7.5：结构化 visual_error + 宿主接管出路（撤销自动兜底）
            meta["visual_error"] = {
                "provider": enrich.vlm.vision_conf()["provider"],
                "error": str(err),
                "host_hint": "宿主多模态可自行 get_frame 看图完成读图；"
                             "或按错误指引处理后重跑；手动换供应商用 VISION_PROVIDER",
            }
        except CliError:
            meta["visual_error"] = {"error": str(err)}
        log(f"画面增强失败（{err}），已降级为纯文字结果")


# ---------------------------------------------------------------- URL 路由

_URL_RE = re.compile(r'https?://[^\s<>"）)]+')


def classify_target(target) -> str:
    """目标 → 'bilibili' | 'xiaohongshu' | 'unknown'（带链接看域名，裸文本看 BV/av 号）。"""
    match = _URL_RE.search(target.strip())
    if match:
        try:
            host = urllib.parse.urlsplit(match.group(0)).hostname or ""
        except ValueError:
            return "unknown"
        if host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com") \
                or host in ("xhslink.com", "www.xhslink.com", "xhslink.cn", "www.xhslink.cn"):
            return "xiaohongshu"
        if host == "bilibili.com" or host.endswith(".bilibili.com") or host == "b23.tv":
            return "bilibili"
        return "unknown"
    if bilibili.BV_RE.search(target) or bilibili.AV_RE.search(target):
        return "bilibili"
    return "unknown"


def run(target, page=None, engine="auto", lang=None, visual=False, work_dir=None,
        skip_asr=False, no_gate=False, reuse_cache=True) -> Result:
    visual = normalize_visual(visual)
    kind = classify_target(target)
    if kind == "xiaohongshu":
        return run_xiaohongshu(target, engine=engine, lang=lang, visual=visual,
                               work_dir=work_dir, skip_asr=skip_asr, no_gate=no_gate,
                               reuse_cache=reuse_cache)
    if kind == "unknown":
        raise CliError(f"认不出这是哪个平台的链接: {target}", 2)
    return run_bilibili(target, page=page, engine=engine, lang=lang, visual=visual,
                        work_dir=work_dir, skip_asr=skip_asr, no_gate=no_gate,
                        reuse_cache=reuse_cache)


def run_xiaohongshu(target, engine="auto", lang=None, visual=False, work_dir=None,
                    skip_asr=False, no_gate=False, reuse_cache=True) -> Result:
    visual = normalize_visual(visual)
    note_id, url, info, auth_mode = xiaohongshu.acquire_note(target)
    meta = {
        "platform": "xiaohongshu",
        "note_id": note_id,
        "url": url,
        "auth_mode": auth_mode,
        "title": info["title"],
        "desc": info["desc"],
        "owner": info["owner"],
        "duration": info["duration"],
    }
    log(f"{meta['title']} · {meta['owner'] or '作者未记录'} · {meta['duration'] or '?'}s · "
        f"笔记 {note_id}（{auth_mode}）")

    # ---- --visual：视频只下载一次（请求预算纪律），供取帧与 ASR 回退共用 ----
    run_dir = video_path = None
    if visual:
        run_dir = _visual_run_dir(meta, work_dir)
        try:
            video_path = enrich.video.fetch_xhs_video(note_id, info["video_urls"],
                                                      run_dir / "download",
                                                      reuse_cache=reuse_cache,
                                                      cache_base=work_dir)
        except CliError as err:
            meta["frames"] = []
            meta["visual_error"] = str(err)
            log(f"视频下载失败（{err}），画面增强跳过（transcript 不受影响）")

    # ---- 内嵌字幕优先（复用 validate 四规则）----
    try:
        sub = xiaohongshu.pick_subtitle(info["subtitles"])
        if sub:
            t0 = time.time()
            raw = xiaohongshu.download_srt(sub["url"])
            if raw:
                segs = segs_from_subtitle(raw)
                failures = validate.validate(
                    segs, meta["duration"], title=meta["title"], desc=meta["desc"])
                if not failures:
                    log(f"命中内嵌字幕 {sub['language']}（{len(segs)} 段）")
                    meta["subtitle_lan"] = sub["language"]
                    if video_path is not None:
                        read_frames(meta, run_dir, video_path, mode=visual, no_gate=no_gate)
                    return Result(
                        Transcript(segs, lang=sub["language"] or lang or "auto",
                                   engine="subtitle", source="subtitle",
                                   elapsed_s=time.time() - t0),
                        meta,
                        run_dir=run_dir,
                    )
                meta["validate_warnings"] = failures
                for fail in failures:
                    log(f"字幕校验未过: {fail}，疑似污染字幕，降级走本地 ASR")
            else:
                log("内嵌字幕内容为空，降级走本地 ASR")
        else:
            log("无内嵌字幕，走本地 ASR")
    except CliError as err:
        if err.code == 4:
            raise  # 网络风控如实上报，不降级
        log(f"字幕链路异常（{err}），降级走本地 ASR")

    # ---- 本地 ASR 回退：视频流 → ffmpeg 16k mono → ASR ----
    if skip_asr:  # --type slides（S7）：内容在画面，不做语音转写
        log("slides 模式：跳过 ASR，transcript 标 visual-only（要保险改用 --type mixed）")
        transcript = Transcript([], lang=lang or "auto", engine="none",
                                source="visual-only", elapsed_s=0.0)
        note_suspected_silence(transcript, meta, visual=visual)
        meta["asr_engine"] = "skipped"
        if video_path is not None:
            read_frames(meta, run_dir, video_path, mode=visual, no_gate=no_gate)
        meta.setdefault("timings", {})["transcript_s"] = 0.0
        return Result(transcript, meta, run_dir=run_dir)
    eng = pick_engine(engine)
    with tempfile.TemporaryDirectory(prefix="bili-digest-") as td:
        if video_path is not None:
            log(f"复用已下载视频转 16k mono → {eng.name} 转写")
            wav = Path(td) / f"audio_xhs_{note_id}.wav"
            audio.to_wav16k(video_path, wav)  # 无返回值，产物路径即 wav
        else:
            log(f"下载视频流 → ffmpeg 16k mono → {eng.name} 转写")
            wav = xiaohongshu.fetch_audio(note_id, info["video_urls"], Path(td))
        transcript = eng.transcribe(wav, lang=lang)
    if not transcript.segments:
        raise CliError("ASR 没有产出任何片段", 3)
    note_suspected_silence(transcript, meta, visual=visual)
    meta["asr_engine"] = eng.name
    if video_path is not None:
        read_frames(meta, run_dir, video_path, mode=visual, no_gate=no_gate)
    return Result(transcript, meta, run_dir=run_dir)


def run_bilibili(target, page=None, engine="auto", lang=None, visual=False, work_dir=None,
                 skip_asr=False, no_gate=False, reuse_cache=True) -> Result:
    visual = normalize_visual(visual)
    cred = bilibili.maybe_refresh(bilibili.load_cred())
    bvid = bilibili.resolve_bvid(target)
    view = bilibili.video_view(bvid)

    pages = view.get("pages") or []
    page_no = page or bilibili.page_hint(target) or 1
    if page_no < 1 or page_no > max(1, len(pages)):
        raise CliError(f"没有第 {page_no} P（共 {len(pages)} P）", 2)
    cur = pages[page_no - 1] if pages else {}
    cid = cur.get("cid") or view["cid"]
    duration = cur.get("duration") or view.get("duration")

    meta = {
        "bvid": bvid,
        "aid": view.get("aid"),
        "cid": cid,
        "page": page_no,
        "page_part": cur.get("part"),
        "title": view.get("title"),
        "desc": view.get("desc"),
        "owner": (view.get("owner") or {}).get("name"),
        "duration": duration,
    }
    log(f"{meta['title']} · P{page_no} · {duration}s · {bvid}")

    # 凭据优先级：扫码 credentials.json（带 refresh_token 自动续期）> 插件导出的 BILI_COOKIE
    cookie = bilibili.cookie_header(cred["cookies"]) if cred else bilibili.cookie_from_keys()

    def enrich_visual():
        """--visual（B站）：视频轨独立于音轨，transcript 就绪后再下载取帧。失败降级。"""
        run_dir = _visual_run_dir(meta, work_dir)
        try:
            stream_info = {}
            t0 = time.time()
            video_path = enrich.video.fetch_bilibili_video(bvid, cid, run_dir / "download",
                                                           cookie=cookie, info=stream_info,
                                                           reuse_cache=reuse_cache,
                                                           cache_base=work_dir)
            meta["video_stream"] = stream_info  # 所选流与体积（S6.3 资源画像）
            meta.setdefault("timings", {})["download_video_s"] = round(time.time() - t0, 1)
        except CliError as err:
            meta["frames"] = []
            meta["visual_error"] = str(err)
            log(f"视频下载失败（{err}），画面增强跳过（transcript 不受影响）")
            return run_dir
        read_frames(meta, run_dir, video_path, mode=visual, no_gate=no_gate)
        return run_dir

    # ---- 字幕优先（含污染校验降级）----
    try:
        tracks = bilibili.subtitle_tracks(bvid, cid, cookie=cookie)
        track = bilibili.pick_track(tracks)
        if track:
            t0 = time.time()
            raw = bilibili.download_subtitle(track)
            if raw:
                segs = segs_from_subtitle(raw)
                failures = validate.validate(segs, duration, title=meta["title"], desc=meta["desc"])
                if not failures:
                    log(f"命中字幕轨 {track.get('lan')}（{len(segs)} 段）")
                    meta["subtitle_lan"] = track.get("lan")
                    run_dir = enrich_visual() if visual else None
                    meta.setdefault("timings", {})["transcript_s"] = round(time.time() - t0, 1)
                    return Result(
                        Transcript(segs, lang=track.get("lan") or lang or "auto",
                                   engine="subtitle", source="subtitle",
                                   elapsed_s=time.time() - t0),
                        meta,
                        run_dir=run_dir,
                    )
                meta["validate_warnings"] = failures
                for fail in failures:
                    log(f"字幕校验未过: {fail}，疑似污染字幕，降级走本地 ASR")
            else:
                log("字幕轨内容为空，降级走本地 ASR")
        else:
            log("无可用字幕轨" + ("（未登录，公开字幕也探测不到时可先 digest login）" if not cred else ""))
    except CliError as err:
        if err.code == 4:
            raise  # 网络风控如实上报，不降级
        log(f"字幕链路异常（{err}），降级走本地 ASR")

    # ---- 本地 ASR 回退 ----
    if skip_asr:  # --type slides（S7）：内容在画面，不做语音转写
        log("slides 模式：跳过 ASR，transcript 标 visual-only（要保险改用 --type mixed）")
        transcript = Transcript([], lang=lang or "auto", engine="none",
                                source="visual-only", elapsed_s=0.0)
        note_suspected_silence(transcript, meta, visual=visual)
        meta["asr_engine"] = "skipped"
        run_dir = enrich_visual() if visual else None
        meta.setdefault("timings", {})["transcript_s"] = 0.0
        return Result(transcript, meta, run_dir=run_dir)
    eng = pick_engine(engine)
    log(f"下载音频 → ffmpeg 16k mono → {eng.name} 转写")
    with tempfile.TemporaryDirectory(prefix="bili-digest-") as td:
        wav = audio.fetch_audio(bvid, cid, Path(td), cookie=cookie)
        transcript = eng.transcribe(wav, lang=lang)
    if not transcript.segments:
        raise CliError("ASR 没有产出任何片段", 3)
    note_suspected_silence(transcript, meta, visual=visual)
    meta["asr_engine"] = eng.name
    run_dir = enrich_visual() if visual else None
    meta.setdefault("timings", {})["transcript_s"] = round(transcript.elapsed_s, 1)
    return Result(transcript, meta, run_dir=run_dir)


# ---------------------------------------------------------------- 输出格式

def join_lines(transcript: Transcript) -> str:
    """逐段拼接，丢掉与上一段完全相同的行（AI 字幕 / whisper 尾部幻觉常见）。"""
    lines, prev = [], None
    for seg in transcript.segments:
        text = seg.text.strip()
        if not text or text == prev:
            continue
        lines.append(text)
        prev = text
    return "\n".join(lines)


def render_txt(transcript: Transcript) -> str:
    return join_lines(transcript)


def render_srt(transcript: Transcript) -> str:
    def stamp(sec):
        ms = int(round(sec * 1000))
        return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)

    blocks = []
    for seg in transcript.segments:
        if not seg.text.strip():
            continue
        blocks.append(f"{len(blocks) + 1}\n{stamp(seg.start)} --> {stamp(seg.end)}\n{seg.text.strip()}")
    return "\n\n".join(blocks)


def render_md(transcript: Transcript, meta: dict) -> str:
    def clock(sec):
        return "%02d:%02d" % (int(sec) // 60, int(sec) % 60)

    head = [f"# {meta.get('title') or meta.get('bvid') or meta.get('note_id')}", ""]
    facts = [
        (f"BV: {meta.get('bvid')}" if meta.get("bvid") else f"笔记: {meta.get('note_id')}"),
        (f"P: {meta.get('page')}" if meta.get("bvid") else None),
        f"时长: {meta.get('duration')}s",
        f"引擎: {transcript.engine}（{transcript.source}）",
        f"语言: {transcript.lang}",
        f"耗时: {transcript.elapsed_s:.1f}s",
    ]
    head.append("- " + " · ".join(str(f) for f in facts if f))
    head.append("")
    body = [f"[{clock(seg.start)}] {seg.text.strip()}"
            for seg in transcript.segments if seg.text.strip()]
    return "\n".join(head + body)


def render_json(transcript: Transcript, meta: dict) -> str:
    payload = dict(meta)
    payload.update({
        "lang": transcript.lang,
        "engine": transcript.engine,
        "source": transcript.source,
        "elapsed_s": round(transcript.elapsed_s, 3),
        "segments": [{"start": s.start, "end": s.end, "text": s.text}
                     for s in transcript.segments],
        "text": join_lines(transcript),
    })
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render(transcript: Transcript, meta: dict, fmt: str) -> str:
    if fmt == "txt":
        return render_txt(transcript)
    if fmt == "srt":
        return render_srt(transcript)
    if fmt == "md":
        return render_md(transcript, meta)
    if fmt == "json":
        return render_json(transcript, meta)
    raise CliError(f"未知格式: {fmt}（支持 txt|srt|md|json）", 2)
