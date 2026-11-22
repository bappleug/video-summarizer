"""frames 单测：场景检测/采点约束/pHash 去重/抽帧规格（ffmpeg 合成视频，全程无网络）。"""

import subprocess
from pathlib import Path

import pytest

from bili_digest.enrich import frames
from bili_digest.errors import CliError


def make_scene_video(path, colors=("red", "blue", "0x00FF00"), seconds=3, size="320x240"):
    """多段纯色视频（每段先落 finite mp4 再 concat demuxer），段间硬切 → 场景检测的确定切点。
    坑：lavfi 输入必须逐段 -t 截断（无限流 + 输出 -t 会得到整段同色）；
    颜色避开 CSS green（其 luma 76 与 red 相同，灰度 pHash 区分不开），用纯绿 0x00FF00。"""
    path = Path(path)
    clips = []
    for i, color in enumerate(colors):
        clip = path.with_name(f"clip{i}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"color=c={color}:s={size}:r=10", "-t", str(seconds), str(clip)],
            check=True,
        )
        clips.append(clip)
    listing = path.with_name("list.txt")
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(path)],
        check=True,
    )
    return path


def make_solid_video(path, color="gray", seconds=12):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=320x240:r=10", "-t", str(seconds), str(path)],
        check=True,
    )
    return path


def jpeg_size(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, check=True,
    )
    w, h = result.stdout.decode().split(",")
    return int(w), int(h)


# ---------------------------------------------------------------- 场景分数（逐帧全量输出）

def test_scene_scores_finds_color_switches(tmp_path):
    video = make_scene_video(tmp_path / "scene.mp4", seconds=3)
    scores = frames.scene_scores(video)
    assert len(scores) > 50  # 9s @10fps 逐帧输出
    big = [t for t, s in scores if s >= 0.3]
    assert any(abs(t - 3.0) < 0.6 for t in big)   # red→blue
    assert any(abs(t - 6.0) < 0.6 for t in big)   # blue→green


def test_scene_scores_solid_video_yields_no_spikes(tmp_path):
    """纯色视频分数恒 0（median=MAD=0 → 自适应阈值 0）→ 零尖刺 → 下游回退网格。"""
    video = make_solid_video(tmp_path / "solid.mp4")
    assert frames.detect_spikes(frames.scene_scores(video), floor=frames.PPT_SPIKE_FLOOR) == []


def test_scene_scores_rejects_garbage(tmp_path):
    bad = tmp_path / "fake.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(CliError) as err:
        frames.scene_scores(bad)
    assert err.value.code == 1


def test_probe_duration(tmp_path):
    video = make_solid_video(tmp_path / "solid.mp4", seconds=2)
    assert 1.5 < (frames.probe_duration(video) or 0) < 2.5


# ---------------------------------------------------------------- 尖刺检测 + 采点（纯函数，S6.2）

def _fake_scores(spikes: dict, total=20.0, step=0.1, background=0.0):
    """伪分数序列：恒背景 + 指定时刻尖刺（合成视频/伪序列做无网络单测的注入点）。"""
    scores = [(round(i * step, 3), background) for i in range(int(total / step) + 1)]
    for t, s in spikes.items():
        scores[int(round(t / step))] = (t, s)
    return scores


def test_detect_spikes_adaptive_catches_sub_floor_spikes():
    """自适应触发：全零背景（median=MAD=0 → 阈值 0）下 0.005 尖刺低于两模式底线仍被抓。"""
    scores = _fake_scores({5.0: 0.005})
    assert frames.detect_spikes(scores, floor=frames.AUTO_SPIKE_FLOOR) == [5.0]


def test_detect_spikes_floor_gates_mode_sensitivity():
    """模式敏感度：自适应 p99 被高事件带抬到 0.02（0.010 尖刺低于它）时，底线决定
    有效阈值——ppt 0.008 连元素显现级 0.010 尖刺也抓，auto 0.015 只抓 0.02 级尖刺。"""
    scores = _fake_scores({5.0: 0.010, 10.0: 0.010, 15.0: 0.02, 20.0: 0.02, 25.0: 0.02, 30.0: 0.02},
                          total=32)
    assert frames.detect_spikes(scores, floor=frames.PPT_SPIKE_FLOOR) == [5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    assert frames.detect_spikes(scores, floor=frames.AUTO_SPIKE_FLOOR) == [15.0, 20.0, 25.0, 30.0]


def test_detect_spikes_no_false_negative_at_or_above_floor():
    """设计不变式守护：不低于模式底线的状态变化尖刺必被捕获，无论全局分布把自适应阈值抬多高。"""
    noisy = [(round(i * 0.04, 3), 0.006 if i % 2 else 0.0) for i in range(251)]
    noisy[125] = (5.0, frames.AUTO_SPIKE_FLOOR)  # 恰好等于底线（底线条件含等号）
    assert frames.detect_spikes(noisy, floor=frames.AUTO_SPIKE_FLOOR) == [5.0]


def test_detect_spikes_merges_burst_taking_peak():
    """连刺合并：窗口内连刺（链式续簇）取峰顶算一次；间隔 >0.5s 分开计。"""
    scores = _fake_scores({10.0: 0.2, 10.3: 0.5, 10.7: 0.3, 20.0: 0.4})
    assert frames.detect_spikes(scores, floor=0.1) == [10.3, 20.0]


def test_plan_candidates_spikes_sampling_offsets():
    """主路采样：尖刺 t 取 t-0.25s（变化前=该状态最全帧）、首尖刺钳到 0、片尾补 duration-0.3s。"""
    scores = _fake_scores({0.1: 0.9, 10.0: 0.9, 30.0: 0.9, 60.0: 0.9}, total=100)
    times, sampling = frames.plan_candidates(scores, 100, mode="ppt")
    assert sampling == "spikes"
    assert times == [0.0, 9.75, 29.75, 59.75, 99.7]


def test_plan_candidates_one_spike_plus_tail_stay_on_spike_path():
    """1 尖刺 + 片尾 = 2 候选 → 仍走主路：双状态短视频（如 red|blue 硬切）回退粗网格
    会丢状态（假阴性无补救），主路 2 候选恰好覆盖两个稳定状态。"""
    scores = _fake_scores({3.0: 0.9}, total=6)
    times, sampling = frames.plan_candidates(scores, 6, mode="auto")
    assert sampling == "spikes" and times == [2.75, 5.7]


def test_plan_candidates_two_spikes_plus_tail_stay_on_spike_path():
    """2 尖刺 + 片尾 = 3 候选 → 主路覆盖 3 个稳定状态（不因回退网格丢中间状态）。"""
    scores = _fake_scores({3.0: 0.9, 6.0: 0.9}, total=9)
    times, sampling = frames.plan_candidates(scores, 9, mode="auto")
    assert sampling == "spikes" and times == [2.75, 5.75, 8.7]


def test_plan_candidates_fallback_grid_when_quiet():
    """候选不足（尖刺+片尾 <3，近静止）→ 回退 S6.1 网格：ppt 5s 密于 auto 10s；时长未知单帧。"""
    scores = _fake_scores({})  # 全零背景 → 0 尖刺，仅片尾 1 候选
    ppt_times, ppt_sampling = frames.plan_candidates(scores, 60, mode="ppt")
    assert ppt_sampling == "uniform" and ppt_times[0] == 0.0
    assert all(b - a <= 5.0 + 1e-6 for a, b in zip(ppt_times, ppt_times[1:]))
    auto_times, _ = frames.plan_candidates(scores, 60, mode="auto")
    assert len(auto_times) < len(ppt_times)  # auto 间隔更疏
    assert frames.plan_candidates(scores, None)[0] == [0.0]  # 时长未知退化为单帧


def test_plan_candidates_caps_candidates():
    spikes = {float(t): 0.9 for t in range(1, 401)}  # 400 尖刺 → 采样点超硬顶
    times, sampling = frames.plan_candidates(_fake_scores(spikes, total=400), 400, mode="ppt")
    assert sampling == "spikes" and len(times) <= frames.MAX_CANDIDATES
    assert times[0] == 0.75  # 保首
    # 保尾：末样本落在片尾窗口内（片尾 399.7 或更后的末尖刺前样本 399.75）
    assert times[-1] >= 400 - frames.TAIL_OFFSET


def test_plan_candidates_rejects_unknown_mode():
    with pytest.raises(CliError) as err:
        frames.plan_candidates(_fake_scores({}), 10, mode="story")
    assert err.value.code == 2


# ---------------------------------------------------------------- cap 伸缩 + 静态簇优先（S6.3）

def test_scale_cap():
    """cap 按时长伸缩：min(60, max(基础, ceil(d/30)))；短视频与时长未知行为不变。"""
    assert frames.scale_cap(frames.MAX_FRAMES, 6) == frames.MAX_FRAMES       # 短视频不变
    assert frames.scale_cap(frames.PPT_MAX_FRAMES, 381) == frames.PPT_MAX_FRAMES  # < 基础不变
    assert frames.scale_cap(frames.MAX_FRAMES, 900) == 30                     # 15min → 30
    assert frames.scale_cap(frames.MAX_FRAMES, 1800) == frames.CAP_CEILING    # 30min → 60
    assert frames.scale_cap(frames.PPT_MAX_FRAMES, 3360) == frames.CAP_CEILING  # 56min → 60
    assert frames.scale_cap(frames.MAX_FRAMES, None) == frames.MAX_FRAMES


def test_static_priority_thin_keeps_static_thins_dynamic():
    """静态簇优先抽稀：密集动态段（间隔 <2s）→ 每 30s 至多 1 代表；静态页（≥2s）全保留。"""
    K = frames.KeyFrame
    dyn = [K(t=round(0.5 + 1.4 * i, 2), fid=f"d{i}", path=None) for i in range(71)]  # 0-100s
    static = [K(t=t, fid=f"s{int(t)}", path=None)
              for t in (149.75, 299.75, 449.75, 579.75, 619.7)]
    kept = frames.static_priority_thin([*dyn, *static], cap=21)
    ts = [kf.t for kf in kept]
    for t in (149.75, 299.75, 449.75, 579.75, 619.7):
        assert t in ts  # 静态页一个不丢
    assert len([t for t in ts if t < 110]) == 4  # 动态段 4 个 30s 桶代表
    assert len(ts) == 9


def test_static_priority_thin_uniform_when_statics_overflow():
    """静态帧自身超 cap → 按时间均匀抽稀（保首保尾）。"""
    items = [frames.KeyFrame(t=float(t * 10), fid=f"s{t}", path=None) for t in range(40)]
    kept = frames.static_priority_thin(items, cap=8)
    assert len(kept) == 8 and kept[0].t == 0.0 and kept[-1].t == 390.0


def test_static_priority_thin_passthrough():
    """不超 cap 原样返回。"""
    items = [frames.KeyFrame(t=1.0, fid="a", path=None),
             frames.KeyFrame(t=2.0, fid="b", path=None)]
    assert frames.static_priority_thin(items, cap=5) == items


# ---------------------------------------------------------------- 长簇时间片切分（S6.4）

def test_select_cluster_frames_splits_long_clusters_by_span():
    """簇数 < cap 且存在长簇（跨度 >30s）→ 剩余配额按有效跨度比例切分补帧。"""
    C = frames._Cand
    short = [C(t=1.0, path=None, h=1)]                                   # 跨度 0：不切
    long1 = [C(t=100.0 + i * 30, path=None, h=1) for i in range(12)]     # 跨度 330s
    long2 = [C(t=600.0 + i * 10, path=None, h=1) for i in range(10)]     # 跨度 90s
    picked = frames.select_cluster_frames([short, long1, long2], max_frames=15)
    ts = [c.t for c in picked]
    # 配额 15-3=12：weight 300:60 → long1 切 11 片、long2 切 3 片 → 1+11+3=15 帧
    assert len(ts) == 15 and 1.0 in ts
    assert len([t for t in ts if 100 <= t <= 430]) == 11
    assert len([t for t in ts if 600 <= t <= 690]) == 3
    assert ts == sorted(ts)  # 时间升序


def test_select_cluster_frames_no_split_without_headroom_or_span():
    """无剩余配额或无长簇（短视频/普通 PPT）→ 每簇一帧，行为不变。"""
    C = frames._Cand
    clusters = [[C(t=float(t), path=None, h=1)] for t in (1, 2, 3)]
    assert len(frames.select_cluster_frames(clusters, 40)) == 3
    long_cl = [C(t=100.0 + i * 30, path=None, h=1) for i in range(12)]
    assert len(frames.select_cluster_frames([long_cl], 1)) == 1  # cap 已满：不切


def test_extract_keyframes_ppt_mode_disables_long_cluster_split(tmp_path, monkeypatch):
    """ppt 模式页面级最全态语义：长簇不切分（S6.4 回归保障，束水攻沙 32 帧基线）；
    auto 模式同样输入会切分补帧。"""
    video = make_solid_video(tmp_path / "solid.mp4", seconds=200)
    fake = _fake_scores({float(t): 0.9 for t in range(10, 200, 10)}, total=200)
    monkeypatch.setattr(frames, "scene_scores", lambda v: fake)
    monkeypatch.setattr(frames, "phash", lambda path: 1)  # 全部候选链成一个长簇
    ppt_frames, _ = frames.extract_keyframes(video, tmp_path / "fp", duration=200, mode="ppt")
    auto_frames, _ = frames.extract_keyframes(video, tmp_path / "fa", duration=200, mode="auto")
    assert len(ppt_frames) == 1          # ppt：长簇只保留最全（最末）一帧
    assert len(auto_frames) > 1          # auto：长簇被切分补帧


# ---------------------------------------------------------------- 抽帧 + 聚簇取末

def test_extract_keyframes_end_to_end(tmp_path):
    video = make_scene_video(tmp_path / "scene.mp4", seconds=3)
    out = tmp_path / "frames"
    keyframes, stats = frames.extract_keyframes(video, out, duration=9)
    assert 2 <= len(keyframes) <= frames.MAX_FRAMES
    assert [kf.fid for kf in keyframes] == [f"f{i:02d}" for i in range(len(keyframes))]
    for kf in keyframes:
        assert kf.path.exists()
        assert kf.path.read_bytes()[:2] == b"\xff\xd8"  # JPEG magic
        assert kf.path.parent == out
    assert stats["mode"] == "auto" and stats["sampling"] == "spikes"
    assert stats["candidates"] >= stats["clusters"] >= len(keyframes)


def test_grab_frame_scales_long_edge(tmp_path):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=red:s=1280x720:r=10", "-t", "1", str(tmp_path / "hd.mp4")],
        check=True,
    )
    out = tmp_path / "frame.jpg"
    frames.grab_frame(tmp_path / "hd.mp4", 0.0, out)
    w, h = jpeg_size(out)
    assert w == frames.LONG_EDGE and h == 378  # 720p → 长边 672、比例保持


def test_phash_same_content_far_apart(tmp_path):
    """pHash 确定性：同内容两图 hash 相同、不同内容汉明距离大。"""
    video = make_scene_video(tmp_path / "scene.mp4", seconds=3)
    a = frames.grab_frame(video, 0.2, tmp_path / "a.jpg")   # red
    b = frames.grab_frame(video, 1.5, tmp_path / "b.jpg")   # red（另一时刻同画面）
    c = frames.grab_frame(video, 4.0, tmp_path / "c.jpg")   # blue
    ha, hb, hc = frames.phash(a), frames.phash(b), frames.phash(c)
    assert ha is not None and hb is not None and hc is not None
    assert frames.hamming(ha, hb) < frames.PHASH_HAMMING
    assert frames.hamming(ha, hc) >= frames.PHASH_HAMMING


def test_extract_keyframes_cluster_keeps_last(tmp_path, monkeypatch):
    """聚簇取末：纯色视频 + 伪造尖刺分数（画面无变化的假阳性）→ 同一簇，保留最末帧
    （片尾 11.7s）；假阳性重复候选被聚簇洗掉。"""
    video = make_solid_video(tmp_path / "solid.mp4", seconds=12)
    monkeypatch.setattr(frames, "scene_scores",
                        lambda v: _fake_scores({2.0: 0.9, 7.0: 0.9}, total=12))
    keyframes, stats = frames.extract_keyframes(video, tmp_path / "frames", duration=12)
    assert [kf.fid for kf in keyframes] == ["f00"]
    assert keyframes[0].t == 11.7  # 候选 [1.75, 6.75, 11.7]（含片尾）全同簇 → 取最末
    assert not list((tmp_path / "frames").glob("cand_*.jpg"))  # 候选临时文件已清理
    assert stats["largest_cluster_ratio"] == 1.0  # 门控信号：全部候选落同一簇


def test_extract_keyframes_ratio_below_gate_for_diverse_content(tmp_path, monkeypatch):
    """三色场景视频 → 多簇，最大簇占比 < 0.8（不触发低多样性门控）。"""
    video = make_scene_video(tmp_path / "scene.mp4", seconds=3)
    keyframes, stats = frames.extract_keyframes(video, tmp_path / "frames", duration=9)
    assert stats["clusters"] >= 2
    assert stats["largest_cluster_ratio"] is not None and stats["largest_cluster_ratio"] < 0.8


def test_extract_keyframes_mode_caps(tmp_path, monkeypatch):
    """模式帧数上限（短视频回归）：400s 内 auto 20 / ppt 40 不受伸缩影响
    （伪造尖刺分数 + 两两远距 hash → 每候选独立成簇）。"""
    video = make_solid_video(tmp_path / "solid.mp4", seconds=400)
    fake = _fake_scores({float(t): 0.9 for t in range(5, 400, 5)}, total=400)  # 79 尖刺+片尾=80 候选
    monkeypatch.setattr(frames, "scene_scores", lambda v: fake)
    seq = iter(range(10000))
    far, near = (1 << 62) - 1, 0  # 汉明距离 62 ≫ 阈值 → 必开新簇

    def alternating_hash(path):
        return far if next(seq) % 2 else near
    monkeypatch.setattr(frames, "phash", alternating_hash)
    auto_frames, auto_stats = frames.extract_keyframes(video, tmp_path / "fa", duration=400)
    ppt_frames, ppt_stats = frames.extract_keyframes(video, tmp_path / "fp", duration=400,
                                                     mode="ppt")
    assert len(auto_frames) == frames.MAX_FRAMES and auto_stats["mode"] == "auto"
    assert len(ppt_frames) == frames.PPT_MAX_FRAMES and ppt_stats["mode"] == "ppt"
    assert auto_frames[0].path.exists() and ppt_frames[-1].path.exists()  # 落盘帧真实存在


def test_extract_keyframes_static_priority_mixed_long_video(tmp_path, monkeypatch):
    """S6.3 混合形态长视频（合成）：0-100s 密集动态段 + 散布静态页 → 静态页全保留、
    动态段抽稀为每 30s 一代表（伪造尖刺分数 + 交替远距 hash → 每候选独立成簇）。"""
    video = make_solid_video(tmp_path / "solid.mp4", seconds=620)
    spikes = {round(0.6 + 1.4 * i, 2): 0.9 for i in range(71)}  # 动态段（采样间隔 1.4s < 2s）
    spikes.update({150.0: 0.9, 300.0: 0.9, 450.0: 0.9, 580.0: 0.9})  # 散布静态页
    monkeypatch.setattr(frames, "scene_scores", lambda v: _fake_scores(spikes, total=620))
    seq = iter(range(10000))
    far, near = (1 << 62) - 1, 0

    def alternating_hash(path):
        return far if next(seq) % 2 else near
    monkeypatch.setattr(frames, "phash", alternating_hash)
    keyframes, stats = frames.extract_keyframes(video, tmp_path / "frames", duration=620)
    # cap = scale_cap(20, 620) = 21；77 簇 > 21 → 静态优先抽稀
    ts = [kf.t for kf in keyframes]
    for t in (149.75, 299.75, 449.75, 579.75, 619.7):  # 4 静态页 + 片尾全保留
        assert t in ts
    assert len([t for t in ts if t < 110]) == 4  # 动态段仅 4 个桶代表
    assert len(ts) == 9 and len(ts) <= frames.scale_cap(frames.MAX_FRAMES, 620)
    assert stats["scan_s"] >= 0 and stats["grab_s"] >= 0  # 资源画像字段


def test_pick_cluster_frame_arbitration_by_edge_density(tmp_path, monkeypatch):
    """簇内仲裁：末帧边缘密度明显低于簇内峰值（疑似转场模糊/收尾清空）→ 换密度最高帧。"""
    cands = [frames._Cand(t=float(t), path=tmp_path / f"c{t}.jpg", h=1)
             for t in (10, 20, 30)]
    for c in cands:
        c.path.write_bytes(b"\xff\xd8filler")
    densities = {10: 500.0, 20: 400.0, 30: 100.0}  # 末帧最空
    monkeypatch.setattr(frames, "edge_density", lambda p: densities[int(p.stem[1:])])
    assert frames.pick_cluster_frame(cands).t == 10.0
    densities[30] = 460.0  # 末帧密度达到峰值 92% ≥ 0.85 → 保最末
    assert frames.pick_cluster_frame(cands).t == 30.0
    densities[30] = None  # 密度信号缺失 → 保最末（不猜）
    assert frames.pick_cluster_frame(cands).t == 30.0


def test_pick_cluster_frame_singleton_and_tie(monkeypatch):
    """单成员簇直接取该帧；无密度信号（并列未知）保最末。"""
    monkeypatch.setattr(frames, "edge_density", lambda p: None)
    solo = frames._Cand(t=1.0, path=None, h=1)
    assert frames.pick_cluster_frame([solo]) is solo
    cands = [frames._Cand(t=float(t), path=None, h=1) for t in (1, 2, 3)]
    assert frames.pick_cluster_frame(cands) is cands[-1]  # 密度信号缺失 → 保最末


def test_cluster_candidates_chains_progressive_states():
    """链式聚簇：渐进累积的小步差异连成同页状态链，跨页大差异开新簇。"""
    full = (1 << 62) - 1
    h = [full, full ^ 0b11, full ^ 0b1111, 1]  # 前三两两差 ≤4，末帧与倒数差 ≫ 阈值
    cands = [frames._Cand(t=float(i), path=None, h=v) for i, v in enumerate(h)]
    clusters = frames.cluster_candidates(cands)
    assert [len(cl) for cl in clusters] == [3, 1]
    assert clusters[0][-1].t == 2.0  # 簇内最新成员是比较锚点
