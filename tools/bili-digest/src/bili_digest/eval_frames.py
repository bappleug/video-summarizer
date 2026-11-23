"""取帧质量评测 harness（S6.4）：金标时间线 / 记分卡 / contact sheet。

用法（stdlib，评测场景可用真实 VLM，花费控制在验收预算内）：
  python -m bili_digest.eval_frames --gold <视频文件或run目录>   # 稠密撒帧全量分类 → runs/eval-frames/<id>/gold.json
  python -m bili_digest.eval_frames --extract <视频文件> --mode auto|ppt  # 当前代码离线重提帧 → <id>/current/
  python -m bili_digest.eval_frames --score <run目录>            # run 保留帧 vs gold → 记分卡（stdout + scorecard.json）
  python -m bili_digest.eval_frames --sheet <run目录>            # 单页 HTML 缩略图网格（人审 30 秒级）

指标定义（打分核心为纯函数，合成数据单测覆盖）：
- 覆盖率 coverage：金标内容状态（同 frame_type 连续段，边界外扩半个采样间隔）被
  run 内容帧命中的比例；
- 精度 precision：run 保留帧中内容帧（非 stage_talking）占比；
- 冗余度 redundancy：每命中区间的平均内容帧数；
- 真冗余 redundancy_truth：同金标状态内保留帧逐对 pHash 汉明距离——≤8 近似重复
  （真冗余/浪费），>8 子状态（金标粒度下的额外信息）；
- 人脸帧 face_frames：保留的 stage_talking 帧数（验收 ≤1，含封面候选）。
--score 对旧 run（meta 无 type 字段）会补一次 VLM 分类（仅对已保留帧，花费记入记分卡）。
金标间隔自适应：长视频 12s；短视频按 duration/50 收紧（下限 1s）——12s 对 ≤2min
视频（如 15 页快闪 PPT）采不出状态。
"""

import argparse
import json
import sys
from pathlib import Path

from . import config
from .enrich import frames as frames_mod
from .enrich import vlm
from .enrich.frames import KeyFrame
from .errors import CliError, log

GOLD_INTERVAL = 12.0  # 金标采样间隔上限（秒，长视频）
REDUNDANT_HAMMING = 8  # 同状态内 pHash 汉明距离 ≤ 此值 = 真冗余（近似重复）


def adaptive_interval(duration) -> float:
    """金标采样间隔：min(12, max(1, duration/50))——短视频收紧到状态可分辨。"""
    return min(GOLD_INTERVAL, max(1.0, (duration or 0) / 50))


# ---------------------------------------------------------------- 纯函数（单测覆盖）

def content_intervals(samples, interval=GOLD_INTERVAL, duration=None):
    """金标样本 → 内容状态区间 [{start, end, type}]：**同 frame_type 连续段**合并为一个
    状态（desktop→diagram 的类型切换即状态边界；stage_talking 段为非内容态，不输出），
    边界外扩半个采样间隔（钳到 [0, duration]）。samples 需带 t 与 frame_type。"""
    if not samples:
        return []
    dur = duration if duration is not None else max(s["t"] for s in samples) + interval
    runs, cur = [], None
    for s in sorted(samples, key=lambda x: x["t"]):
        ftype = s.get("frame_type") or "other"
        if cur is not None and cur[2] == ftype:
            cur[1] = s["t"]
        else:
            if cur is not None:
                runs.append(cur)
            cur = [s["t"], s["t"], ftype]
    if cur is not None:
        runs.append(cur)
    half = interval / 2
    return [{"start": max(0.0, a - half), "end": min(dur, b + half), "type": ft}
            for a, b, ft in runs if ft != "stage_talking"]


def score_frames(kept_frames, gold):
    """run 保留帧 vs 金标 → 记分卡 dict。kept_frames 需带 t（type 缺失按 other=内容）。"""
    intervals = gold.get("content_intervals") or []
    content = [f for f in kept_frames if (f.get("type") or "other") != "stage_talking"]
    stage = [f for f in kept_frames if f.get("type") == "stage_talking"]
    hits = sum(1 for a, b in ((iv["start"], iv["end"]) for iv in intervals)
               if any(a <= f["t"] <= b for f in content))
    dist = {}
    for f in kept_frames:
        ftype = f.get("type") or "other"
        dist[ftype] = dist.get(ftype, 0) + 1
    return {
        "coverage": round(hits / len(intervals), 3) if intervals else None,
        "precision": round(len(content) / len(kept_frames), 3) if kept_frames else None,
        "face_frames": len(stage),
        "redundancy": round(len(content) / hits, 3) if hits else None,
        "kept": len(kept_frames),
        "content_frames": len(content),
        "gold_intervals": len(intervals),
        "intervals_hit": hits,
        "type_distribution": dist,
    }


# ---------------------------------------------------------------- gold：稠密金标

def build_gold(target, out_root=None, interval=None) -> Path:
    """视频（或 run 目录，取 download/ 下视频）→ 按自适应间隔撒帧全量分类，
    落 runs/eval-frames/<id>/gold.json（含 samples 与 content_intervals）。"""
    video = _resolve_video(target)
    vid = video.stem
    out_dir = Path(out_root) if out_root else config.runs_dir() / "eval-frames" / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = frames_mod.probe_duration(video) or 0.0
    if not duration:
        raise CliError(f"拿不到视频时长: {video}", 3)
    step = interval or adaptive_interval(duration)
    times = [round(i * step, 2) for i in range(int(duration / step) + 1)]
    log(f"金标采样 {len(times)} 点（间隔 {step:.1f}s，时长 {duration:.0f}s）→ {out_dir}")
    keyframes = []
    for i, t in enumerate(times):
        path = out_dir / f"gold_{i:03d}.jpg"
        try:
            frames_mod.grab_frame(video, t, path)
        except CliError:
            continue
        keyframes.append(KeyFrame(t=t, fid=f"g{i:03d}", path=path))
    descs, usage = vlm.describe_frames(keyframes)
    samples = [{"t": d["t"], "frame_type": d.get("frame_type") or "other",
                "desc": d["desc"]} for d in descs]
    gold = {
        "video": str(video), "duration": duration, "interval": step,
        "samples": samples,
        "content_intervals": content_intervals(samples, step, duration),
        "usage": usage,
    }
    out = out_dir / "gold.json"
    out.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"金标时间线 {len(gold['content_intervals'])} 个内容区间 → {out}（¥{usage['cost_yuan']:.4f}）")
    return out


# ---------------------------------------------------------------- current 重提帧 + 冗余真伪（S6.5）

def extract_current(video, mode="auto", title=None, owner=None) -> Path:
    """当前代码对本地视频离线重提帧（评测胶水，不改管线）：取帧+聚簇+长簇切分 →
    VLM 分类 → stage 过滤 → runs/eval-frames/<id>/current/（meta 与 score/sheet 兼容）。"""
    from . import pipeline as pipeline_mod  # 借 filter_stage_frames，零管线改动
    video = Path(video)
    out_dir = config.runs_dir() / "eval-frames" / video.stem / "current"
    duration = frames_mod.probe_duration(video) or 0.0
    keyframes, stats = frames_mod.extract_keyframes(
        video, out_dir / "frames", duration=duration, mode=mode)
    descs, usage = vlm.describe_frames(keyframes, title=title, owner=owner)
    kept, dist = pipeline_mod.filter_stage_frames(keyframes, descs)
    meta = {
        "title": title or video.stem, "duration": duration, "mode": mode,
        "video": str(video),
        "frames": [{"id": kf.fid, "t": desc["t"], "file": f"frames/{kf.path.name}",
                    "desc": desc["desc"], "has_text": bool(desc.get("has_text")),
                    "type": desc.get("frame_type") or "other"} for kf, desc in kept],
        "frame_types": dist,
        "thin_funnel": {"candidates": stats.get("candidates"), "clusters": stats.get("clusters"),
                        "after_thin": stats.get("after_thin"), "after_vlm": len(descs),
                        "kept": len(kept)},
        "vision": usage,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    log(f"current 提帧 {len(kept)} 帧（{mode} 模式，漏斗 {meta['thin_funnel']['candidates']}"
        f"→{meta['thin_funnel']['kept']}）→ {out_dir}")
    return out_dir


def redundancy_truth(run_dir, kept_frames, gold, threshold=REDUNDANT_HAMMING) -> dict:
    """同金标内容状态内的保留内容帧逐对 pHash 汉明距离：≤threshold 真冗余（近似重复），
    >threshold 子状态（金标粒度下的额外信息）。回答「多帧是浪费还是子状态」。"""
    content = sorted((f for f in kept_frames if (f.get("type") or "other") != "stage_talking"),
                     key=lambda f: f["t"])
    hashes = {}
    for f in content:
        h = frames_mod.phash(Path(run_dir) / Path(f["file"]))
        if h is not None:
            hashes[round(f["t"], 3)] = h
    true_n = sub_n = 0
    for iv in gold.get("content_intervals") or []:
        ts = sorted(t for t in hashes if iv["start"] <= t <= iv["end"])
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                if frames_mod.hamming(hashes[ts[i]], hashes[ts[j]]) <= threshold:
                    true_n += 1
                else:
                    sub_n += 1
    pairs = true_n + sub_n
    return {"pairs": pairs, "true_redundant": true_n, "substate": sub_n,
            "true_ratio": round(true_n / pairs, 3) if pairs else None}


# ---------------------------------------------------------------- score / sheet

def _resolve_video(target) -> Path:
    target = Path(target)
    if target.is_dir():
        meta_p = target / "meta.json"
        if meta_p.exists():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            src = meta.get("video")
            if src and Path(src).exists():  # eval current/ 目录：meta 记源视频
                return Path(src)
        videos = sorted((target / "download").glob("*.mp4")) or sorted(target.glob("*.mp4"))
        if not videos:
            raise CliError(f"{target} 下没有视频文件（download/*.mp4）", 2)
        return videos[0]
    if target.is_file():
        return target
    raise CliError(f"找不到 {target}", 2)


def _load_gold(video_path) -> dict:
    gold_path = config.runs_dir() / "eval-frames" / video_path.stem / "gold.json"
    if not gold_path.exists():
        raise CliError(f"缺金标 {gold_path}，先跑 --gold {video_path}", 2)
    return json.loads(gold_path.read_text(encoding="utf-8"))


def score_run(run_dir, classify_missing=True) -> dict:
    """run 目录 → 记分卡（stdout 打印 + 落 scorecard.json）。旧 run 帧无 type 时
    补一次 VLM 分类（只对已保留帧，不动 run 的 meta）。"""
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    kept = meta.get("frames") or []
    extra_usage = None
    if classify_missing and kept and not all("type" in f for f in kept):
        log(f"run 帧缺 frame_type（旧管线产物），补分类 {len(kept)} 帧")
        keyframes = [KeyFrame(t=f["t"], fid=f["id"], path=run_dir / f["file"]) for f in kept]
        descs, extra_usage = vlm.describe_frames(keyframes, title=meta.get("title"))
        kept = [{**f, "type": d.get("frame_type") or "other"} for f, d in zip(kept, descs)]
    gold = _load_gold(_resolve_video(run_dir))
    card = score_frames(kept, gold)
    card["redundancy_truth"] = redundancy_truth(run_dir, kept, gold)  # S6.5 冗余真伪
    card.update({"run": str(run_dir), "classified_posthoc": bool(extra_usage)})
    if extra_usage:
        card["classify_cost_yuan"] = extra_usage["cost_yuan"]
    card["run_vision_cost_yuan"] = (meta.get("vision") or {}).get("cost_yuan")
    card["gold_cost_yuan"] = (gold.get("usage") or {}).get("cost_yuan")
    out = run_dir / "scorecard.json"
    out.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"记分卡 → {out}")
    return card


_TYPE_COLORS = {"stage_talking": "#c0392b", "desktop_content": "#1665d8",
                "diagram": "#0f7b4f", "other": "#666666"}


def write_sheet(run_dir) -> Path:
    """run 目录 → 单页 HTML contact sheet（缩略图网格 + 时间码 + 类型徽标 + 描述首行）。"""
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    cells = []
    for f in meta.get("frames") or []:
        t = f["t"]
        mm, ss = divmod(int(t), 60)
        ftype = f.get("type") or "other"
        desc = (f.get("desc") or "").strip().splitlines()[0][:80]
        color = _TYPE_COLORS.get(ftype, "#666")
        cells.append(
            f'<figure><img src="{f["file"]}">'
            f'<figcaption><span class="tc">[{mm:02d}:{ss:02d}]</span> '
            f'<span class="badge" style="background:{color}">{ftype}</span>'
            f'<div class="desc">{desc}</div></figcaption></figure>')
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>contact sheet</title><style>"
        "body{font-family:-apple-system,sans-serif;margin:16px;background:#fafafa}"
        "h1{font-size:16px}p{color:#555;font-size:12px}"
        "div.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}"
        "figure{margin:0;background:#fff;border:1px solid #ddd;border-radius:6px;overflow:hidden}"
        "img{width:100%;display:block}figcaption{padding:6px 8px;font-size:12px}"
        ".tc{font-family:monospace;font-weight:600;margin-right:6px}"
        ".badge{color:#fff;border-radius:3px;padding:1px 6px;font-size:11px}"
        ".desc{color:#444;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        "</style></head><body>"
        f"<h1>{meta.get('title') or run_dir.name} · contact sheet</h1>"
        f"<p>{len(cells)} 帧 · run {run_dir.name} · 红色=stage_talking 蓝=desktop_content "
        f"绿=diagram 灰=other</p><div class='grid'>{''.join(cells)}</div></body></html>")
    out = run_dir / "contact_sheet.html"
    out.write_text(html, encoding="utf-8")
    log(f"contact sheet → {out}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="取帧质量评测 harness（S6.4/S6.5）")
    ap.add_argument("--gold", metavar="视频或run目录", help="稠密金标：自适应间隔撒帧全量分类")
    ap.add_argument("--extract", metavar="视频文件", help="当前代码离线重提帧（评测胶水）")
    ap.add_argument("--mode", choices=("auto", "ppt"), default="auto", help="--extract 的视觉模式")
    ap.add_argument("--score", metavar="run目录", help="run 保留帧 vs gold → 记分卡")
    ap.add_argument("--sheet", metavar="run目录", help="生成单页 contact sheet HTML")
    args = ap.parse_args(argv)
    if not (args.gold or args.score or args.sheet or args.extract):
        ap.error("需要 --gold / --extract / --score / --sheet 之一")
    if args.gold:
        build_gold(args.gold)
    if args.extract:
        extract_current(args.extract, mode=args.mode)
    if args.score:
        card = score_run(args.score)
        print(json.dumps(card, ensure_ascii=False, indent=2))
    if args.sheet:
        write_sheet(args.sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
