"""pipeline 单测：引擎选择 + 输出格式渲染（不触网）。"""

import json

import pytest

from bili_digest import pipeline
from bili_digest.errors import CliError
from bili_digest.transcribe import CloudSfTranscriber, MlxTranscriber
from bili_digest.transcribe.base import Segment, Transcript


def make_transcript():
    segs = [
        Segment(start=0.0, end=2.0, text="你好"),
        Segment(start=2.0, end=4.0, text="你好"),   # 连续重复行
        Segment(start=4.0, end=6.0, text=""),
        Segment(start=6.0, end=8.5, text="世界"),
    ]
    return Transcript(segs, lang="zh", engine="test", source="subtitle", elapsed_s=1.0)


META = {"bvid": "BV1CV411s7jd", "page": 1, "title": "测试视频", "duration": 9}


def test_render_txt_drops_dup_and_empty():
    assert pipeline.render_txt(make_transcript()) == "你好\n世界"


def test_render_srt_format():
    srt = pipeline.render_srt(make_transcript())
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 3
    assert blocks[0].startswith("1\n00:00:00,000 --> 00:00:02,000\n你好")
    assert blocks[2].startswith("3\n00:00:06,000 --> 00:00:08,500\n世界")


def test_render_md():
    md = pipeline.render_md(make_transcript(), META)
    assert md.splitlines()[0] == "# 测试视频"
    assert "[00:00] 你好" in md
    assert "[00:06] 世界" in md


def test_render_json_roundtrip():
    payload = json.loads(pipeline.render_json(make_transcript(), META))
    assert payload["bvid"] == "BV1CV411s7jd"
    assert payload["text"] == "你好\n世界"
    assert payload["segments"][0] == {"start": 0.0, "end": 2.0, "text": "你好"}
    assert payload["source"] == "subtitle"


def test_render_unknown_format():
    with pytest.raises(CliError) as err:
        pipeline.render(make_transcript(), META, "pdf")
    assert err.value.code == 2


def test_pick_engine_auto_mlx_available(monkeypatch):
    monkeypatch.setattr(MlxTranscriber, "available", lambda self: True)
    eng = pipeline.pick_engine("auto")
    assert isinstance(eng, MlxTranscriber)
    assert pipeline.pick_engine("mlx").name == "mlx-whisper"


def test_pick_engine_auto_falls_back_to_cloud(monkeypatch):
    monkeypatch.setattr(MlxTranscriber, "available", lambda self: False)
    monkeypatch.setattr(CloudSfTranscriber, "available", lambda self: True)
    assert isinstance(pipeline.pick_engine("auto"), CloudSfTranscriber)


def test_pick_engine_auto_no_engine(monkeypatch):
    monkeypatch.setattr(MlxTranscriber, "available", lambda self: False)
    monkeypatch.setattr(CloudSfTranscriber, "available", lambda self: False)
    with pytest.raises(CliError) as err:
        pipeline.pick_engine("auto")
    assert err.value.code == 3
    assert "SILICONFLOW_API_KEY" in str(err.value)
    assert "digest doctor" in str(err.value)


def test_pick_engine_mlx_unavailable(monkeypatch):
    monkeypatch.setattr(MlxTranscriber, "available", lambda self: False)
    with pytest.raises(CliError) as err:
        pipeline.pick_engine("mlx")
    assert err.value.code == 3
    assert "cloud" in str(err.value)


def test_pick_engine_cloud_needs_key(monkeypatch):
    monkeypatch.setattr(CloudSfTranscriber, "available", lambda self: False)
    with pytest.raises(CliError) as err:
        pipeline.pick_engine("cloud")
    assert err.value.code == 3
    assert "SILICONFLOW_API_KEY" in str(err.value)


def test_pick_engine_cloud_ok(monkeypatch):
    monkeypatch.setattr(CloudSfTranscriber, "available", lambda self: True)
    assert pipeline.pick_engine("cloud").name == "siliconflow-sensevoice"


def test_pick_engine_unknown():
    with pytest.raises(CliError) as err:
        pipeline.pick_engine("sensevoice")
    assert err.value.code == 2


# ---------------------------------------------------------------- URL 路由（S4）

@pytest.mark.parametrize("target,expected", [
    ("BV1CV411s7jd", "bilibili"),
    ("av170001", "bilibili"),
    ("https://www.bilibili.com/video/BV1CV411s7jd?p=5", "bilibili"),
    ("https://b23.tv/abcd123", "bilibili"),
    ("m.bilibili.com 链接带 m 子域也算", "unknown"),          # 无 scheme → 当裸文本处理
    ("https://m.bilibili.com/video/BV1CV411s7jd", "bilibili"),
    ("https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9?xsec_token=T", "xiaohongshu"),
    ("https://www.xiaohongshu.com/discovery/item/6411cf99000000001300b6d9", "xiaohongshu"),
    ("https://xhslink.cn/aBc123", "xiaohongshu"),
    ("https://xhslink.com/aBc123", "xiaohongshu"),
    ("看看这个 https://xhslink.cn/a/ 好看", "xiaohongshu"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "unknown"),
    ("随便一句话", "unknown"),
])
def test_classify_target(target, expected):
    assert pipeline.classify_target(target) == expected


def test_run_unknown_target_rejected():
    with pytest.raises(CliError) as err:
        pipeline.run("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert err.value.code == 2


# ---------------------------------------------------------------- 无语音传导（修 1）


def test_note_suspected_silence_logs_hint_and_meta(capsys):
    """疑似无语音：stderr 一行指引（--visual 后续版本支持）+ 记入 meta；由调用方保证不改退出码。"""
    tr = Transcript([Segment(i * 30.0, i * 30.0 + 30.0, "字幕志愿者 杨茜茜")
                     for i in range(4)],
                    lang="zh", engine="mlx-whisper", source="asr", elapsed_s=5.2,
                    suspected_silent=True)
    meta = {}
    pipeline.note_suspected_silence(tr, meta)
    err = capsys.readouterr().err
    assert "疑似无语音" in err
    assert "该视频可能没有语音，内容可能在画面文字中" in err
    assert "--visual" in err and "帧描述会成为报告主内容源" in err
    assert "当前未加 --visual" in err
    assert meta["suspected_silent"] is True


def test_note_suspected_silence_noop_for_normal():
    meta = {}
    pipeline.note_suspected_silence(make_transcript(), meta)
    assert "suspected_silent" not in meta


# ---------------------------------------------------------------- 视觉增强编排（S6）

import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

from bili_digest import enrich  # noqa: E402


def make_two_color_video(path):
    """red(3s)+blue(3s) + 静音轨：1 个确定场景切点（坑：lavfi 需逐段 -t，见 test_frames；
    无音轨会让 to_wav16k 报「不含任何流」，故补 anullsrc）。"""
    clips = []
    for i, color in enumerate(("red", "blue")):
        clip = Path(path).with_name(f"c{i}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"color=c={color}:s=320x240:r=10", "-t", "3", str(clip)], check=True)
        clips.append(clip)
    listing = Path(path).with_name("lst.txt")
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", str(path)], check=True)
    return Path(path)


class FakeEngine:
    name = "fake-asr"

    def __init__(self, wav_path):
        self.wav_path = wav_path

    def transcribe(self, wav, lang="zh"):
        assert Path(wav) == Path(self.wav_path) or Path(wav).exists()
        return Transcript([Segment(0.0, 5.0, "幻觉循环"), Segment(5.0, 10.0, "幻觉循环"),
                           Segment(10.0, 15.0, "幻觉循环"), Segment(15.0, 20.0, "幻觉循环")],
                          lang=lang, engine=self.name, source="asr", elapsed_s=0.1,
                          suspected_silent=True)


def xhs_env(monkeypatch, tmp_path, video):
    """mock 小红书取材：无字幕 → ASR 回退；--visual 视频只下载一次。"""
    info = {"title": "自然地理基础", "desc": "经纬网", "owner": "匠心地理", "duration": 6.0,
            "video_urls": ["https://xhs/video.mp4"], "subtitles": []}
    downloads = {"n": 0}

    def fake_fetch_xhs(note_id, urls, work_dir):
        downloads["n"] += 1
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        out = Path(work_dir) / f"xhs_{note_id}.mp4"
        out.write_bytes(video.read_bytes())
        return out

    monkeypatch.setattr(pipeline.xiaohongshu, "acquire_note",
                        lambda target: ("note1", "https://xhs/note1", info, "cookie"))
    monkeypatch.setattr(enrich.video, "fetch_xhs_video", fake_fetch_xhs)
    return downloads


def fake_describe(monkeypatch, probe_verdict="demo"):
    """mock VLM：读图固定回报帧内容；门控探测回报指定判定（默认内容演示型，不拦截）。"""
    def _describe(frames, title=None, owner=None, pause=0.5):
        descs = [{"t": kf.t, "desc": f"帧内容{kf.fid}", "has_text": True} for kf in frames]
        usage = {"provider": "zhipu", "model": "glm-5.3-flash", "frames": len(frames),
                 "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                 "cost_yuan": 0.0, "elapsed_s": 0.01}
        return descs, usage
    monkeypatch.setattr(enrich.vlm, "describe_frames", _describe)
    monkeypatch.setattr(enrich.vlm, "probe_content_type",
                        lambda frames, title=None, owner=None:
                        (probe_verdict, {"prompt_tokens": 30, "completion_tokens": 5}))


def test_run_xiaohongshu_visual_full_chain(monkeypatch, tmp_path):
    """--visual 全链路（无网络）：视频单次下载 → 取帧 → VLM → meta 携带 frames/vision。"""
    video = make_two_color_video(tmp_path / "src.mp4")
    downloads = xhs_env(monkeypatch, tmp_path, video)
    fake_describe(monkeypatch)
    wav_holder = {}

    class Holder:
        name = "fake-asr"

        def transcribe(self, wav, lang="zh"):
            wav_holder["wav"] = Path(wav)
            assert wav_holder["wav"].exists()  # 临时目录内的 wav 在转写时必须真实存在
            return FakeEngine(wav).transcribe(wav, lang)

    monkeypatch.setattr(pipeline, "pick_engine", lambda name: Holder())

    result = pipeline.run("https://xhslink.cn/abc", visual=True, work_dir=tmp_path / "run")
    assert downloads["n"] == 1  # 视频只下载一次（请求预算纪律）
    assert wav_holder["wav"].suffix == ".wav"  # ASR 复用本地视频转出的 wav（临时目录内已验证存在）
    assert result.run_dir == tmp_path / "run"
    assert (result.run_dir / "download").exists() and (result.run_dir / "frames").exists()
    assert len(list((result.run_dir / "frames").glob("f*.jpg"))) >= 1
    frames = result.meta["frames"]
    assert frames and frames[0]["file"].startswith("frames/f") and "desc" in frames[0]
    assert result.meta["vision"]["model"] == "glm-5.3-flash"
    assert result.meta["suspected_silent"] is True  # 幻觉循环被标记（与 --visual 协同）


def test_run_xiaohongshu_visual_download_failure_degrades(monkeypatch, tmp_path):
    """视频下载失败 → 降级为纯文字（meta.visual_error），transcript 不丢。"""
    info = {"title": "T", "desc": "", "owner": "O", "duration": 6.0,
            "video_urls": ["https://xhs/video.mp4"], "subtitles": []}
    monkeypatch.setattr(pipeline.xiaohongshu, "acquire_note",
                        lambda target: ("note1", "https://xhs/note1", info, "cookie"))

    def boom(note_id, urls, work_dir):
        raise CliError("视频流下载失败（HTTP 403）", 4)

    monkeypatch.setattr(enrich.video, "fetch_xhs_video", boom)
    monkeypatch.setattr(pipeline, "pick_engine",
                        lambda name: FakeEngine(tmp_path / "nonexistent.wav"))
    # 无视频可复用 → ASR 走 xiaohongshu.fetch_audio 原路径
    monkeypatch.setattr(pipeline.xiaohongshu, "fetch_audio",
                        lambda note_id, urls, work_dir: (_ for _ in ()).throw(AssertionError("不应再走视频下载")))

    def fake_fetch_audio(note_id, urls, work_dir):
        wav = Path(work_dir) / "a.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=r=16000:d=1", str(wav)], check=True)
        return wav

    monkeypatch.setattr(pipeline.xiaohongshu, "fetch_audio", fake_fetch_audio)
    result = pipeline.run("https://xhslink.cn/abc", visual=True, work_dir=tmp_path / "run2")
    assert result.meta.get("visual_error") and result.meta["frames"] == []
    assert result.transcript.segments  # transcript 完好


def test_run_bilibili_visual_after_subtitle(monkeypatch, tmp_path):
    """B站：字幕命中后 --visual 照常取帧（视频轨独立下载，不影响字幕路径）。"""
    video = make_two_color_video(tmp_path / "bili_src.mp4")

    class FakeBili:
        BV_RE = pipeline.bilibili.BV_RE
        AV_RE = pipeline.bilibili.AV_RE

    monkeypatch.setattr(pipeline.bilibili, "maybe_refresh", lambda cred: None)
    monkeypatch.setattr(pipeline.bilibili, "load_cred", lambda: None)
    monkeypatch.setattr(pipeline.bilibili, "resolve_bvid", lambda t: "BV1x")
    monkeypatch.setattr(pipeline.bilibili, "page_hint", lambda t: None)
    monkeypatch.setattr(pipeline.bilibili, "video_view",
                        lambda bvid: {"title": "B站测试", "cid": 7, "duration": 6,
                                      "pages": [{"cid": 7, "duration": 6, "part": "P1"}]})
    monkeypatch.setattr(pipeline.bilibili, "subtitle_tracks", lambda bvid, cid, cookie=None: [])
    monkeypatch.setattr(pipeline.bilibili, "download_subtitle",
                        lambda track: [{"from": 0, "to": 5, "text": "字幕内容"}])
    monkeypatch.setattr(pipeline.bilibili, "pick_track",
                        lambda tracks, want=None: {"lan": "zh-CN", "subtitle_url": "x"})
    fake_describe(monkeypatch)
    monkeypatch.setattr(pipeline.validate, "validate", lambda *a, **kw: [])  # 本测试不关心污染校验

    def fake_fetch_bili(bvid, cid, work_dir, cookie=None, info=None):
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        out = Path(work_dir) / f"{bvid}_{cid}.mp4"
        out.write_bytes(video.read_bytes())
        return out

    monkeypatch.setattr(enrich.video, "fetch_bilibili_video", fake_fetch_bili)
    result = pipeline.run_bilibili("BV1x", visual=True, work_dir=tmp_path / "run3")
    assert result.transcript.source == "subtitle"
    assert result.meta["frames"]  # 字幕命中也完成画面增强
    assert result.run_dir == tmp_path / "run3"


def test_note_suspected_silence_with_visual(monkeypatch, capsys):
    """--visual 下的无语音提示：不再说「未加 --visual」，改为帧描述主导的协同说明。"""
    tr = Transcript([Segment(0.0, 30.0, "循环"), Segment(30.0, 60.0, "循环"),
                     Segment(60.0, 90.0, "循环")],
                    lang="zh", engine="mlx-whisper", source="asr", elapsed_s=1.0,
                    suspected_silent=True)
    meta = {}
    pipeline.note_suspected_silence(tr, meta, visual=True)
    err = capsys.readouterr().err
    assert "已启用 --visual" in err and "帧描述将成为报告主内容源" in err
    assert "未加" not in err


# ---------------------------------------------------------------- 画面价值门控（S6.1）

def test_read_frames_low_diversity_gate_skips_vlm(monkeypatch, tmp_path, capsys):
    """本地预筛：最大簇占比 ≥ 0.8 → 跳过全部 VLM（含探测），meta 记 low_diversity。"""
    video = make_two_color_video(tmp_path / "src.mp4")
    kfs = [enrich.frames.KeyFrame(t=float(i), fid=f"f{i:02d}",
                                  path=tmp_path / f"f{i}.jpg") for i in range(3)]
    stats = {"mode": "auto", "sampling": "cuts", "candidates": 5, "clusters": 2,
             "largest_cluster_ratio": 0.8}  # 恰在阈值上 → 命中
    monkeypatch.setattr(enrich.frames, "extract_keyframes", lambda *a, **kw: (kfs, stats))

    def must_not_call(*a, **kw):
        raise AssertionError("门控命中后不应触达 VLM")
    monkeypatch.setattr(enrich.vlm, "describe_frames", must_not_call)
    monkeypatch.setattr(enrich.vlm, "probe_content_type", must_not_call)
    meta = {}
    pipeline.read_frames(meta, tmp_path, video, mode="auto")
    assert meta["frames"] == [] and meta["visual_gate"] == "low_diversity"
    assert "低视觉多样性" in capsys.readouterr().err


def test_read_frames_low_diversity_threshold_not_triggered(monkeypatch, tmp_path):
    """占比 0.79（阈值下）→ 不门控，进入探测 + 读图（探测成本并入 vision 记账）。"""
    video = make_two_color_video(tmp_path / "src.mp4")
    kfs = [enrich.frames.KeyFrame(t=float(i), fid=f"f{i:02d}",
                                  path=tmp_path / f"f{i}.jpg") for i in range(3)]
    stats = {"mode": "auto", "sampling": "cuts", "candidates": 5, "clusters": 3,
             "largest_cluster_ratio": 0.79}
    monkeypatch.setattr(enrich.frames, "extract_keyframes", lambda *a, **kw: (kfs, stats))
    monkeypatch.setattr(enrich.vlm, "probe_content_type",
                        lambda frames, title=None, owner=None: ("demo", {"prompt_tokens": 30, "completion_tokens": 5}))
    fake_describe(monkeypatch)
    meta = {"title": "T", "duration": 10}
    pipeline.read_frames(meta, tmp_path, video, mode="auto")
    assert meta.get("visual_gate") is None and len(meta["frames"]) == 3
    assert meta["vision"]["prompt_tokens"] == 130  # 100 读图 + 30 探测合并记账


def test_read_frames_talking_head_probe_stops_reading(monkeypatch, tmp_path, capsys):
    """VLM 探测判口播型 → 停止读图，meta 记 talking_head + 探测级 usage。"""
    video = make_two_color_video(tmp_path / "src.mp4")
    kfs = [enrich.frames.KeyFrame(t=float(i), fid=f"f{i:02d}",
                                  path=tmp_path / f"f{i}.jpg") for i in range(4)]
    stats = {"mode": "auto", "sampling": "cuts", "candidates": 6, "clusters": 4,
             "largest_cluster_ratio": 0.5}
    monkeypatch.setattr(enrich.frames, "extract_keyframes", lambda *a, **kw: (kfs, stats))
    probed = {}

    def fake_probe(frames, title=None, owner=None):
        probed["n"] = len(frames)
        return "talking_head", {"prompt_tokens": 2500, "completion_tokens": 10}
    monkeypatch.setattr(enrich.vlm, "probe_content_type", fake_probe)

    def must_not_describe(*a, **kw):
        raise AssertionError("口播型判定后不应继续读图")
    monkeypatch.setattr(enrich.vlm, "describe_frames", must_not_describe)
    meta = {}
    pipeline.read_frames(meta, tmp_path, video, mode="auto")
    assert meta["frames"] == [] and meta["visual_gate"] == "talking_head"
    assert probed["n"] == pipeline.PROBE_FRAMES  # 首/中/尾 3 帧
    assert meta["vision"]["frames"] == 0 and meta["vision"]["gated"] == "talking_head"
    # S7.3：默认 siliconflow 价率未查证 → 成本按 0 估（tokens 照记）；供应商/模型如实记账
    assert meta["vision"]["provider"] == "siliconflow"
    assert meta["vision"]["model"] == "Qwen/Qwen3-VL-8B-Instruct"
    assert "人物口播" in capsys.readouterr().err


def test_read_frames_probe_failure_fails_open(monkeypatch, tmp_path):
    """探测判定不明确（None）→ 按内容型继续读图（不因探测失败误伤）。"""
    video = make_two_color_video(tmp_path / "src.mp4")
    kfs = [enrich.frames.KeyFrame(t=float(i), fid=f"f{i:02d}",
                                  path=tmp_path / f"f{i}.jpg") for i in range(2)]
    stats = {"mode": "auto", "sampling": "cuts", "candidates": 2, "clusters": 2,
             "largest_cluster_ratio": 0.5}
    monkeypatch.setattr(enrich.frames, "extract_keyframes", lambda *a, **kw: (kfs, stats))
    monkeypatch.setattr(enrich.vlm, "probe_content_type",
                        lambda frames, title=None, owner=None: (None, {"prompt_tokens": 0, "completion_tokens": 0}))
    fake_describe(monkeypatch)
    meta = {}
    pipeline.read_frames(meta, tmp_path, video, mode="auto")
    assert meta.get("visual_gate") is None and len(meta["frames"]) == 2


def test_read_frames_ppt_mode_skips_gates(monkeypatch, tmp_path):
    """ppt 模式：低多样性也不门控、不探测（用户显式指定优先），全量读图。"""
    video = make_two_color_video(tmp_path / "src.mp4")
    kfs = [enrich.frames.KeyFrame(t=float(i), fid=f"f{i:02d}",
                                  path=tmp_path / f"f{i}.jpg") for i in range(5)]
    stats = {"mode": "ppt", "sampling": "uniform", "candidates": 10, "clusters": 2,
             "largest_cluster_ratio": 0.9}  # ppt 下高占比照样读图
    monkeypatch.setattr(enrich.frames, "extract_keyframes", lambda *a, **kw: (kfs, stats))

    def must_not_probe(*a, **kw):
        raise AssertionError("ppt 模式不应触发门控探测")
    monkeypatch.setattr(enrich.vlm, "probe_content_type", must_not_probe)
    fake_describe(monkeypatch)
    meta = {}
    pipeline.read_frames(meta, tmp_path, video, mode="ppt")
    assert meta.get("visual_gate") is None and len(meta["frames"]) == 5


def test_normalize_visual_modes():
    assert pipeline.normalize_visual(None) is None
    assert pipeline.normalize_visual(False) is None
    assert pipeline.normalize_visual(True) == "auto"
    assert pipeline.normalize_visual("auto") == "auto"
    assert pipeline.normalize_visual("PPT") == "ppt"
    with pytest.raises(CliError) as err:
        pipeline.normalize_visual("story")
    assert err.value.code == 2


# ---------------------------------------------------------------- S6.4 帧级信息过滤

def test_filter_stage_frames_keeps_earliest_cover_only():
    """stage_talking 全弃但保留至多 1 张最早者当封面候选；内容帧全保留。"""
    kfs = [type("K", (), {"t": t})() for t in (3, 13, 24, 402)]
    descs = [{"frame_type": "stage_talking"}, {"frame_type": "stage_talking"},
             {"frame_type": "desktop_content"}, {"frame_type": "diagram"}]
    kept, dist = pipeline.filter_stage_frames(kfs, descs)
    assert [k.t for k, _ in kept] == [3, 24, 402]
    assert dist == {"stage_talking": 2, "desktop_content": 1, "diagram": 1}


def test_filter_stage_frames_content_budget_thins():
    """内容帧超预算 → 静态优先抽稀（全静态 → 均匀保首尾）。"""
    kfs = [type("K", (), {"t": float(i * 10)})() for i in range(100)]
    descs = [{"frame_type": "desktop_content"}] * 100
    kept, _ = pipeline.filter_stage_frames(kfs, descs, budget=10)
    assert len(kept) == 10
    assert kept[0][0].t == 0.0 and kept[-1][0].t == 990.0


def test_read_frames_stage_filter_and_funnel(monkeypatch, tmp_path):
    """端到端（mock VLM）：stage 过滤 + meta 抽稀漏斗 + 类型分布；ppt 模式同样过滤。"""
    kfs = [enrich.frames.KeyFrame(t=t, fid=f"f{i:02d}", path=tmp_path / f"f{i}.jpg")
           for i, t in enumerate((3, 13, 402))]
    for kf in kfs:
        kf.path.write_bytes(b"\xff\xd8filler")
    stats = {"mode": "ppt", "sampling": "spikes", "candidates": 9, "clusters": 3,
             "after_thin": 3, "largest_cluster_ratio": 0.3}
    monkeypatch.setattr(enrich.frames, "extract_keyframes", lambda *a, **kw: (kfs, stats))

    def fake_describe(frames, title=None, owner=None, pause=0.5):
        types = {3: "stage_talking", 13: "stage_talking", 402: "desktop_content"}
        descs = [{"t": kf.t, "desc": f"d{kf.t}", "has_text": True,
                  "frame_type": types[kf.t]} for kf in frames]
        usage = {"provider": "zhipu", "model": "glm-5.3-flash", "frames": len(frames),
                 "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
                 "cost_yuan": 0.001, "elapsed_s": 0.01}
        return descs, usage
    monkeypatch.setattr(enrich.vlm, "describe_frames", fake_describe)
    meta = {"duration": 500}
    pipeline.read_frames(meta, tmp_path, tmp_path / "v.mp4", mode="ppt")
    assert [f["t"] for f in meta["frames"]] == [3, 402]  # 两张 stage 只留最早封面
    assert meta["frames"][0]["type"] == "stage_talking"
    assert meta["frame_types"] == {"stage_talking": 2, "desktop_content": 1}
    assert meta["thin_funnel"] == {"candidates": 9, "clusters": 3, "after_thin": 3,
                                   "after_vlm": 3, "kept": 2}


# ---------------------------------------------------------------- S7：type 语义糖衣

def test_type_profile_four_routes():
    assert pipeline.type_profile("talk") == {"visual": None, "skip_asr": False, "no_gate": False}
    assert pipeline.type_profile("slides") == {"visual": "ppt", "skip_asr": True, "no_gate": False}
    assert pipeline.type_profile("mixed") == {"visual": "auto", "skip_asr": False, "no_gate": True}
    assert pipeline.type_profile(None) == pipeline.type_profile("auto")
    with pytest.raises(CliError) as err:
        pipeline.type_profile("story")
    assert err.value.code == 2


def test_run_bilibili_slides_skips_asr(monkeypatch, tmp_path):
    """slides：字幕校验失败后不再回退 ASR，transcript 标 visual-only；read_frames 照常。"""
    video = make_two_color_video(tmp_path / "src.mp4")

    class FakeBili:
        BV_RE = pipeline.bilibili.BV_RE
        AV_RE = pipeline.bilibili.AV_RE
    monkeypatch.setattr(pipeline.bilibili, "maybe_refresh", lambda cred: None)
    monkeypatch.setattr(pipeline.bilibili, "load_cred", lambda: None)
    monkeypatch.setattr(pipeline.bilibili, "resolve_bvid", lambda t: "BV1sk")
    monkeypatch.setattr(pipeline.bilibili, "page_hint", lambda t: None)
    monkeypatch.setattr(pipeline.bilibili, "video_view",
                        lambda bvid: {"title": "T", "cid": 7, "duration": 6,
                                      "pages": [{"cid": 7, "duration": 6, "part": "P1"}]})
    monkeypatch.setattr(pipeline.bilibili, "subtitle_tracks", lambda bvid, cid, cookie=None: [])
    monkeypatch.setattr(pipeline.audio, "fetch_audio",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("slides 不应下载音频")))
    fake_describe(monkeypatch)
    frames_called = {}

    def fake_read_frames(meta, run_dir, video_path, mode="auto", no_gate=False):
        frames_called["mode"] = mode
        meta["frames"] = [{"id": "f00", "t": 1.0, "file": "frames/f00.jpg",
                           "desc": "d", "has_text": True, "type": "diagram"}]
    monkeypatch.setattr(pipeline, "read_frames", fake_read_frames)

    def fake_fetch(bvid, cid, work_dir, cookie=None, info=None):
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        out = Path(work_dir) / f"{bvid}_{cid}.mp4"
        out.write_bytes(video.read_bytes())
        return out
    monkeypatch.setattr(enrich.video, "fetch_bilibili_video", fake_fetch)

    result = pipeline.run_bilibili("BV1sk", visual="ppt", skip_asr=True)
    assert result.transcript.source == "visual-only"
    assert result.transcript.segments == [] and result.transcript.engine == "none"
    assert result.meta["asr_engine"] == "skipped"
    assert frames_called["mode"] == "ppt" and result.meta["frames"]


def test_read_frames_no_gate_bypasses_low_diversity(monkeypatch, tmp_path):
    """mixed（no_gate）：低多样性占比照样读图（双通道保证，同 ppt 禁门控先例）。"""
    kfs = [enrich.frames.KeyFrame(t=float(i), fid=f"f{i:02d}",
                                  path=tmp_path / f"f{i}.jpg") for i in range(3)]
    for kf in kfs:
        kf.path.write_bytes(b"\xff\xd8filler")
    stats = {"mode": "auto", "sampling": "spikes", "candidates": 3, "clusters": 1,
             "after_thin": 3, "largest_cluster_ratio": 0.99}
    monkeypatch.setattr(enrich.frames, "extract_keyframes", lambda *a, **kw: (kfs, stats))
    fake_describe(monkeypatch)
    meta = {"duration": 10}
    pipeline.read_frames(meta, tmp_path, tmp_path / "v.mp4", mode="auto", no_gate=True)
    assert len(meta["frames"]) == 3 and meta.get("visual_gate") is None
