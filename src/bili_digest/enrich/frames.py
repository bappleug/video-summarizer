"""关键帧提取（本地确定性，零网络）：ffmpeg 逐帧场景分数 → 敏感边界（尖刺）检测采样 → 抽帧缩放 → pHash 聚簇取末。

策略（S6.2 敏感边界检测主路，替代盲网格；设计不变式：检测层允许假阳性——多截无害，
交给聚簇清洗，不允许假阴性——漏状态无补救）：
- 检测：逐帧 scene 分数全量输出，双条件触发（满足其一即尖刺）：自适应
  ``score > p99``（等价稳健波动度量；安静视频 p99≈0 → 任何非零分都触发）或
  绝对底线（ppt 0.008 / auto 0.015；实测坑：渐进动画类 PPT 全程 max≈0.047，
  0.3 阈值场景检测零切点）。PPT 同底版下「翻页」与「页内元素显现」的尖刺
  不可分也无需区分——目标是每个视觉稳定状态的最全帧；
- 采样：0.5s 内连刺取峰顶算一次（淡入淡出产生连刺）；每个尖刺 t 取 ``t-0.25s``
  （变化前 = 该状态最全帧）；片尾补 ``duration-0.3s``（最后一页没有下一次翻页来触发）；
  候选不足（零尖刺，近静止视频）→ 回退 S6.1 网格采样（ppt 5s / auto 10s，聚簇兜底）；
- 聚簇取末：8x8 DCT pHash 汉明距离链式聚簇（与簇内最新成员比较，渐进累积状态归同簇），
  每簇保留最末帧（= 页面最全状态），簇数即有效页数；
- cap 伸缩与静态簇优先（S6.3 混合模式）：cap 按时长伸缩（min(60, max(基础, ceil(d/30)))，
  短视频行为不变）；超 cap 抽稀时静态簇（状态稳定 ≥2s，技术视频的信息载体）保配额，
  动态帧先抽稀为每 30s 至多 1 个代表，仍超再均匀抽稀；
- 长簇时间片切分（S6.4 cap 疑点修复）：簇数 < cap 且存在跨度 >30s 的长簇时，剩余配额
  按有效跨度比例切分长簇（片内取最全帧）——录屏类小步渐变连成同簇会把中段桌面状态
  吞没（S6.3 实测 56min 只出 20 簇 < cap 60，瓶颈在聚簇粒度而非 cap）；
- 簇内仲裁：末帧边缘密度（64x64 灰度梯度能量，Sobel-lite，ffmpeg+numpy 本地零成本）
  明显低于簇内峰值（疑似转场模糊/收尾清空）→ 换簇内密度最高帧（同密度取更晚者）；
  numpy 缺失时无 pHash 也就无聚簇（逐帧独立），仲裁自然不触发；
- 帧规格：长边缩到 LONG_EDGE px 的 JPEG（-q:v 4 ≈ 质量 80，控制 VLM token）。
依赖系统 ffmpeg（与 audio.py 同约定）；numpy 非硬依赖（仅 pHash/边缘密度用，可优雅降级）。
"""

import math
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..errors import CliError, log

try:  # pHash 加分项：numpy 已在 venv（mlx extra 传递依赖），核心管线不强求
    import numpy as _np
except ImportError:  # pragma: no cover - venv 内总有 numpy，仅环境裁剪时走到
    _np = None

MAX_FRAMES = 20            # auto 模式基础 cap（S6.1；S6.3 起按时长伸缩，见 scale_cap）
PPT_MAX_FRAMES = 40        # ppt 模式基础 cap（长 PPT 课不漏页：伸缩后可达 60）
CAP_CEILING = 60           # cap 伸缩上限（auto/ppt 同）
CAP_SCALE_INTERVAL = 30    # 每 30s 时长折 1 帧配额：cap = min(上限, max(基础, ceil(d/30)))
STATIC_GAP = 2.0           # 与前帧间隔 ≥ 此值 = 静态簇（该状态稳定 ≥2s，信息载体，保配额）
DYNAMIC_REP_WINDOW = 30.0  # 动态段抽稀密度：每 30s 至多 1 个代表帧
CLUSTER_SPLIT_MIN_SPAN = 30.0  # 簇时间跨度超过此值才参与补帧切分（短视频/普通 PPT 不受影响）
MAX_CANDIDATES = 240       # 采样点硬顶（防超长视频本地耗时失控，超出均匀抽稀保首保尾）
SPIKE_ADAPTIVE_Q = 0.99    # 自适应分位：score > p99（等价稳健波动度量，见 detect_spikes）
PPT_SPIKE_FLOOR = 0.008    # ppt 绝对底线：页内元素显现级低尖刺也要抓（渐进动画分数极低）
AUTO_SPIKE_FLOOR = 0.015   # auto 绝对底线：比 ppt 粗一档，过滤更低的微噪声
SPIKE_MERGE_WINDOW = 0.5   # 连刺合并窗口：0.5s 内连刺取峰顶算一次（淡入淡出产生连刺）
SPIKE_PRE_OFFSET = 0.25    # 尖刺前回退：t-0.25s = 变化前该状态的最全帧
TAIL_OFFSET = 0.3          # 片尾补帧偏移：duration-0.3s（最后一页无下一次翻页触发）
MIN_SPIKE_CANDIDATES = 2   # 尖刺+片尾候选不足此数（=零尖刺，近静止）→ 回退 S6.1 网格采样。
                           # 不取 3：1 尖刺的双状态短视频（如 red|blue 硬切）回退粗网格会丢状态
                           # ——假阴性无补救，优先设计不变式
AUTO_NO_CUT_INTERVAL = 10.0  # 网格回退：auto 采样间隔（近静止/缓变画面，聚簇兜底）
PPT_DENSE_INTERVAL = 5.0     # 网格回退：ppt 采样间隔（同页状态聚簇合并）
LONG_EDGE = 672
JPEG_QSCALE = 4  # mjpeg qscale 2-31（小=高质量），4 ≈ JPEG 质量 80
PHASH_HAMMING = 10
EDGE_ARBITRATION_RATIO = 0.85  # 末帧边缘密度 < 0.85×簇内峰值 → 仲裁换帧

_PTS_TIME_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
_SCORE_RE = re.compile(r"lavfi\.scene_score=(\d+(?:\.\d+)?)")


@dataclass
class KeyFrame:
    t: float
    fid: str    # f00 / f01 …（聚簇后顺序编号，报告嵌入用）
    path: Path  # frames/fNN.jpg 绝对路径


def probe_duration(video) -> float | None:
    """ffprobe 取时长（秒）；拿不到返回 None。"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.decode().strip())
    except ValueError:
        return None


def scene_scores(video) -> list[tuple[float, float]]:
    """逐帧场景分数全量输出 → [(pts_time, score), …]（升序）。
    ``select='gte(scene,0)' + metadata=print``：全帧通过并在 stderr 成对打印
    frame 行与 lavfi.scene_score 行（381s/30fps ≈ 1.1 万点，本地数秒）。
    解码失败抛 CliError（code 1）。"""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video),
         "-vf", "select='gte(scene,0)',metadata=print", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or b"Invalid data" in result.stderr:
        lines = result.stderr.decode("utf-8", "ignore").strip().splitlines()
        raise CliError(f"ffmpeg 场景分数提取失败: {lines[-1][:200] if lines else '未知错误'}", 1)
    scores: list[tuple[float, float]] = []
    cur = None
    for line in result.stderr.decode("utf-8", "ignore").splitlines():
        m = _SCORE_RE.search(line)
        if m:
            if cur is not None:
                scores.append((cur, float(m.group(1))))
            continue
        m = _PTS_TIME_RE.search(line)
        if m:
            cur = float(m.group(1))
    return scores


def _subsample_keep_ends(items: list, n: int) -> list:
    """列表超 cap 时均匀抽取 n 个（保首保尾，保持原序）。"""
    if n <= 1:
        return items[:1]
    idx = sorted({round(i * (len(items) - 1) / (n - 1)) for i in range(n)})
    return [items[i] for i in idx]


def scale_cap(base_cap: int, duration) -> int:
    """cap 按时长伸缩：``min(CAP_CEILING, max(base_cap, ceil(duration/30)))``。
    长视频（30min → 60）不因基础 cap 丢页；短视频 ceil 项小于基础 cap，行为不变。"""
    if not duration or duration <= 0:
        return base_cap
    return min(CAP_CEILING, max(base_cap, math.ceil(duration / CAP_SCALE_INTERVAL)))


def static_priority_thin(items: list, cap: int, static_gap=STATIC_GAP,
                         window=DYNAMIC_REP_WINDOW) -> list:
    """超 cap 抽稀（静态簇优先，S6.3 混合模式）：技术视频的信息载体是静态文本/图例页。
    - 动态帧：与前帧间隔 < static_gap（状态稳定 <2s，密集切变/过渡特征）→ 先抽稀为
      每 window 秒至多 1 个代表（桶内取更晚 = 更全）；
    - 静态帧：间隔 ≥ static_gap（稳定 ≥2s 的页面状态）→ 保配额不弃；
    - 仍超 cap → 全体按时间均匀抽稀（保首保尾）。
    items 只需带 .t（升序）。"""
    if len(items) <= cap:
        return list(items)
    static, dynamic_buckets = [], {}
    for i, it in enumerate(items):
        gap = it.t - (items[i - 1].t if i else 0.0)
        if gap >= static_gap:
            static.append(it)
        else:
            dynamic_buckets[int(it.t // window)] = it  # 桶内取更晚（覆盖写）
    kept = sorted([*static, *dynamic_buckets.values()], key=lambda x: x.t)
    if len(kept) > cap:
        kept = _subsample_keep_ends(kept, cap)
    return kept


def _time_slices(cluster: list, n: int) -> list[list]:
    """簇内候选按序均分 n 片（保序，空片兜底取末元素）。"""
    if n <= 1:
        return [cluster]
    bounds = [round(len(cluster) * i / n) for i in range(n + 1)]
    return [cluster[bounds[i]:bounds[i + 1]] or [cluster[-1]] for i in range(n)]


def select_cluster_frames(clusters: list, max_frames: int, allow_split=True) -> list:
    """簇 → 代表帧（S6.4 cap 疑点修复）：默认每簇 pick_cluster_frame；
    簇数 < max_frames 且存在长簇（跨度 > CLUSTER_SPLIT_MIN_SPAN）时，把剩余配额按
    有效跨度比例给长簇做时间片切分（片内仍取最全帧）。
    实测坑（S6.3 56min 录屏）：scale_cap 已到 60 但链式聚簇只出 20 簇——瓶颈在聚簇
    粒度而非 cap：录屏/演示类内容小步渐变连成同簇，中段桌面状态被「每簇取末」吞没；
    时间片切分让长簇内每个 ≥30s 局部状态都有代表帧。
    ppt 模式应传 allow_split=False（页面级最全态语义，不补页内中间态——束水攻沙
    32 帧基线回归保障）。返回按时间升序的 _Cand 列表。"""
    chosen = [pick_cluster_frame(cl) for cl in clusters]
    headroom = max_frames - len(chosen)
    weights = [max(0.0, cl[-1].t - cl[0].t - CLUSTER_SPLIT_MIN_SPAN) for cl in clusters]
    total = sum(weights)
    if not allow_split or headroom <= 0 or total <= 0:
        return sorted(chosen, key=lambda c: c.t)
    reps: list = []
    budget = headroom  # 比例分母用原始配额（headroom 只做剩余量钳制）
    for cl, w in zip(clusters, weights):
        if w <= 0:
            reps.append(pick_cluster_frame(cl))
            continue
        k = min(headroom, int(round(budget * w / total)))  # 该簇额外片数
        headroom -= k
        reps.extend(pick_cluster_frame(s) for s in _time_slices(cl, k + 1))
    return sorted(reps, key=lambda c: c.t)


def detect_spikes(scores, floor, adaptive_q=SPIKE_ADAPTIVE_Q,
                  merge_window=SPIKE_MERGE_WINDOW) -> list[float]:
    """分数序列 → 合并后尖刺时刻（升序）。纯函数。
    双条件触发（满足其一）：``score > p99``（自适应：等价稳健波动度量——正态校准下
    median+3×1.4826×MAD≈3σ 尾部分位。实测坑：常驻微动画视频（字幕/高亮渐变）的
    median+3×MAD 落在微活动带内（束水攻沙 ≈3.6e-5），连续触发把 0.5s 合并窗口
    链成数十秒巨簇、整段状态被并没；p99 落在事件带上，且安静视频 p99≈0 时任何
    非零分仍触发）或 ``score >= floor``（绝对底线：全局分布整体更高时兜住模式
    敏感度，假阴性无补救）；
    合并窗口内连刺（链式：与前一刺间隔 ≤ merge_window 即续簇）取峰顶算一次。"""
    if not scores:
        return []
    ordered = sorted(s for _, s in scores)
    threshold = ordered[min(len(ordered) - 1, int(adaptive_q * len(ordered)))]
    merged: list[list[float]] = []  # [run_end, peak_t, peak_score]
    for t, s in scores:
        if not (s > threshold or s >= floor):
            continue
        if merged and t - merged[-1][0] <= merge_window:
            run = merged[-1]
            if s > run[2]:
                run[1], run[2] = t, s
            run[0] = t
        else:
            merged.append([t, t, s])
    return [round(run[1], 3) for run in merged]


def plan_candidates(scores, duration, mode="auto") -> tuple[list[float], str]:
    """分数序列 → 采样点规划（纯函数）。返回 (升序时刻列表, 采样策略标签)。
    - 主路（尖刺检测）：每个尖刺 t 取 ``t-0.25s``（变化前=该状态最全帧，钳到 0），
      补片尾 ``duration-0.3s``（最后一页没有下一次翻页来触发）；
    - 候选不足（尖刺+片尾 <2，即零尖刺的近静止视频）→ 回退 S6.1 网格采样
      （ppt 5s / auto 10s，时长未知退化为单帧）；
    - 超过 MAX_CANDIDATES 均匀抽稀（保首保尾）。"""
    if mode not in ("auto", "ppt"):
        raise CliError(f"未知视觉模式: {mode}（支持 auto|ppt）", 2)
    floor = PPT_SPIKE_FLOOR if mode == "ppt" else AUTO_SPIKE_FLOOR
    spikes = detect_spikes(scores, floor)
    times = {round(max(t - SPIKE_PRE_OFFSET, 0.0), 3) for t in spikes}
    if duration:
        times.add(round(duration - TAIL_OFFSET, 3))
    if len(times) < MIN_SPIKE_CANDIDATES:
        if not duration or duration <= 1.0:
            return [0.0], "uniform"
        interval = PPT_DENSE_INTERVAL if mode == "ppt" else AUTO_NO_CUT_INTERVAL
        n = min(MAX_CANDIDATES, max(1, math.ceil(duration / interval)))
        return [round(i * duration / n, 3) for i in range(n)], "uniform"
    ordered = sorted(times)
    if len(ordered) > MAX_CANDIDATES:
        ordered = _subsample_keep_ends(ordered, MAX_CANDIDATES)
    return ordered, "spikes"


def grab_frame(video, t, out_path, long_edge=LONG_EDGE, qscale=JPEG_QSCALE) -> Path:
    """在 t 秒抽 1 帧，长边缩到 long_edge 存 JPEG。失败抛 CliError。"""
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={long_edge}:{long_edge}:force_original_aspect_ratio=decrease",
         "-q:v", str(qscale), str(out_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 500:
        detail = result.stderr.decode("utf-8", "ignore").strip()[-200:]
        raise CliError(f"取帧失败（t={t}s）: {detail}", 1)
    return out_path


# ---------------------------------------------------------------- pHash 与边缘密度（numpy 可选）

def _dct_matrix(n: int):
    """正交 DCT-II 基矩阵（n×n）。"""
    k = _np.arange(n)[:, None].astype(float)
    i = _np.arange(n)[None, :].astype(float)
    m = _np.cos(_np.pi * (2 * i + 1) * k / (2 * n))
    alpha = _np.full(n, math.sqrt(2.0 / n))
    alpha[0] = math.sqrt(1.0 / n)
    return m * alpha[:, None]


_DCT32 = _dct_matrix(32) if _np is not None else None


def phash(img_path) -> int | None:
    """图片 → 63-bit pHash（32x32 灰度 → 2D DCT → 左上 8x8 低频块除 DC 外对中数量化）。
    numpy 缺失或解码失败返回 None（调用方跳过聚簇）。"""
    if _np is None:
        return None
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(img_path),
         "-vf", "scale=32:32", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or len(result.stdout) < 1024:
        return None
    gray = _np.frombuffer(result.stdout[:1024], dtype=_np.uint8).astype(float).reshape(32, 32)
    block = (_DCT32 @ gray @ _DCT32.T)[:8, :8].flatten()[1:]  # 63 个低频系数（DC 不参与）
    bits = 0
    median = float(_np.median(block))
    for coef in block:
        bits = (bits << 1) | (1 if coef > median else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def edge_density(img_path) -> float | None:
    """图片 → 边缘密度（64x64 灰度梯度能量，Sobel-lite：中央差分模长和）。
    文字/图表越满、成像越锐 → 值越高；转场模糊/半空画面低。解码失败返回 None。"""
    if _np is None:
        return None
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(img_path),
         "-vf", "scale=64:64", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or len(result.stdout) < 4096:
        return None
    gray = _np.frombuffer(result.stdout[:4096], dtype=_np.uint8).astype(float).reshape(64, 64)
    gy, gx = _np.gradient(gray)
    return float(_np.sqrt(gx * gx + gy * gy).sum())


# ---------------------------------------------------------------- 聚簇（纯函数，hash 可为 None）

@dataclass
class _Cand:
    t: float
    path: Path
    h: int | None


def cluster_candidates(cands: list[_Cand], threshold=PHASH_HAMMING) -> list[list[_Cand]]:
    """链式聚簇：与当前簇最新成员汉明距离 ≤ threshold 归同簇（渐进累积的小步差异
    连成同页状态链），否则开新簇。hash 为 None 的候选独立成簇（无从比较）。"""
    clusters: list[list[_Cand]] = []
    for c in cands:
        if clusters and c.h is not None and clusters[-1][-1].h is not None \
                and hamming(c.h, clusters[-1][-1].h) <= threshold:
            clusters[-1].append(c)
        else:
            clusters.append([c])
    return clusters


def pick_cluster_frame(cluster: list[_Cand]):
    """簇内仲裁：默认取最末帧（最全状态）；末帧边缘密度明显低于簇内峰值
    （< EDGE_ARBITRATION_RATIO×max，疑似转场模糊/收尾清空）→ 换密度最高帧
    （并列取更晚者，无密度信号保最末）。"""
    if len(cluster) < 2:
        return cluster[-1]
    densities = [(edge_density(c.path), c) for c in cluster]
    last_d = densities[-1][0]
    best = max((d for d, _ in densities if d is not None), default=None)
    if last_d is None or best is None or last_d >= EDGE_ARBITRATION_RATIO * best:
        return cluster[-1]
    top = [c for d, c in densities if d is not None and d == best]
    return top[-1]  # 密度并列取更晚（更接近最全状态）


# ---------------------------------------------------------------- 主入口

def extract_keyframes(video, out_dir, duration=None, mode="auto",
                      max_frames=None) -> tuple[list[KeyFrame], dict]:
    """视频 → (关键帧列表, 聚簇统计)。帧落盘 out_dir/fNN.jpg。

    统计（门控用）：candidates 采样点数 / clusters 簇数 / largest_cluster_ratio
    最大簇占比（hash 可比候选中；口播/访谈特征 ≥0.8 → 调用方跳过 VLM 读图）/
    sampling 采样策略（spikes|uniform）/ scan_s+grab_s 阶段耗时（资源画像）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    video = Path(video)
    duration = duration or probe_duration(video) or 0
    max_frames = max_frames or scale_cap(
        PPT_MAX_FRAMES if mode == "ppt" else MAX_FRAMES, duration)
    t0 = time.time()
    scores = scene_scores(video)
    times, sampling = plan_candidates(scores, duration or None, mode=mode)
    scan_s = time.time() - t0
    log(f"敏感边界检测 → {sampling} 采样 {len(times)} 点（{mode} 模式，扫描 {scan_s:.1f}s）")

    t0 = time.time()
    cands: list[_Cand] = []
    for i, t in enumerate(times):
        cand = out_dir / f"cand_{i:03d}.jpg"
        try:
            grab_frame(video, t, cand)
        except CliError:
            continue  # 个别时间点取帧失败不炸整批（如极尾部）
        cands.append(_Cand(t=round(t, 3), path=cand, h=phash(cand)))
    clusters = cluster_candidates(cands)
    grab_s = time.time() - t0
    selected = select_cluster_frames(clusters, max_frames, allow_split=(mode != "ppt"))
    if len(selected) > max_frames:  # 切分取整可能略超，安全阀
        selected = static_priority_thin(selected, max_frames)
    frames: list[KeyFrame] = []
    for chosen in selected:
        fid = f"f{len(frames):02d}"
        final = out_dir / f"{fid}.jpg"
        chosen.path.replace(final)
        frames.append(KeyFrame(t=chosen.t, fid=fid, path=final))
    selected_paths = {s.path for s in selected}
    for c in cands:  # 清理未选中候选（含长簇切分淘汰的簇内中间态）
        if c.path not in selected_paths:
            c.path.unlink(missing_ok=True)

    hashed = [c for c in cands if c.h is not None]
    largest = max((len(cl) for cl in clusters if cl[0].h is not None), default=0)
    stats = {
        "mode": mode,
        "sampling": sampling,
        "candidates": len(cands),
        "clusters": len(clusters),
        "after_thin": len(frames),  # 抽稀漏斗：本地选帧后帧数（VLM 之前）
        "largest_cluster_ratio": round(largest / len(hashed), 3) if hashed else None,
        "scan_s": round(scan_s, 2),
        "grab_s": round(grab_s, 2),
    }
    log(f"聚簇 {stats['clusters']} 簇（{stats['candidates']} 候选，最大簇占比 "
        f"{stats['largest_cluster_ratio']}）→ 保留 {len(frames)} 帧")
    return frames, stats
