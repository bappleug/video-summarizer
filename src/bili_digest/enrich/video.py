"""视频流下载（--visual 前置）：B站 DASH video 轨 / 小红书 mp4 直链 → run 目录 download/。

与 audio.py 分离（S 交接文档 §4：video 下载与 audio 下载分开，audio.py 保持不动）：
- B站：playurl fnval=16 → dash.video 选轨（S6.3：≤720p 内优先 h264 取最大带宽——读图
  足够、长视频下载体积可控；无 ≤720p 轨退回全列表最大带宽），
  复用 platforms/bilibili.http 下载（UA + Referer，412 风控语义一致）；
- 小红书：masterUrl/backupUrls 直链（h264 优先已在 note_info 排好），
  复用 platforms/xiaohongshu._http_get；m3u8 地址交给 ffmpeg 转存 mp4。

跨 run 缓存（2026-11-22）：视频轨下载到 config.videos_cache_dir()（内容寻址键），
run 目录 download/ 通过硬链接/复制引用缓存文件，同一视频二次运行跳过下载。
reuse_cache=False 可强制重下（仍刷新缓存）。缓存文件带 .part 原子落盘防半成品。
"""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from .. import audio, config
from ..errors import CliError, log
from ..platforms import bilibili, xiaohongshu

MAX_STREAM_HEIGHT = 720  # 选轨清晰度上限（读图足够，56min 级视频体积可控）
MIN_CACHE_BYTES = 1024 * 1024  # 缓存有效性最小体积（1MB），小于此视为半成品


# ---------------------------------------------------------------- 缓存辅助
def _cache_key_bili(bvid, cid, stream_id) -> str:
    """B站缓存键：平台+视频ID+分Pcid+流ID。stream_id 变（清晰度/编码变）即新键。"""
    return f"bili_{bvid}_{cid}_{stream_id or 'na'}"


def _cache_key_xhs(note_id, url) -> str:
    """小红书缓存键：note_id + 首个视频 URL 的哈希前 12 位（区分清晰度/源）。"""
    h = hashlib.sha256((url or "").encode("utf-8")).hexdigest()[:12]
    return f"xhs_{note_id}_{h}"


def _cache_path(key: str, cache_base=None) -> Path:
    cache_dir = config.videos_cache_dir(cache_base)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{key}.mp4"


def _cache_hit(cache_file: Path) -> bool:
    """缓存命中且非半成品（体积达标）。"""
    try:
        return cache_file.exists() and cache_file.stat().st_size >= MIN_CACHE_BYTES
    except OSError:
        return False


def _link_from_cache(cache_file: Path, out_path: Path) -> Path:
    """把缓存文件接入 run 目录 download/：优先硬链接（零拷贝），失败退化复制。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    try:
        os.link(cache_file, out_path)  # 同盘硬链接，不占额外空间
    except OSError:
        shutil.copy2(cache_file, out_path)
    return out_path


def _finish_download(tmp: Path, cache_file: Path, out_path: Path) -> Path:
    """下载完成：原子落缓存（.part → 正式名），再接入 run 目录。"""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, cache_file)  # 原子：避免半成品被当作命中
    return _link_from_cache(cache_file, out_path)


def pick_video_track(videos, max_height=MAX_STREAM_HEIGHT):
    """DASH 视频轨选择：≤max_height 内优先 h264（avc*）取最大带宽；无 ≤max_height 轨
    退回全列表最大带宽（有总比没有好）。"""
    if not videos:
        raise CliError("该视频没有 DASH 视频轨（可能是老 FLV 源），无法提取画面", 3)
    within = [v for v in videos if (v.get("height") or 0) <= max_height]
    pool = within or videos
    h264 = [v for v in pool if str(v.get("codecs") or "").startswith("avc")]
    pool = h264 or pool
    return max(pool, key=lambda v: v.get("bandwidth") or 0)


def _download_bilibili(urls, out_path):
    """下载 DASH 视频轨：用 ffmpeg 而非 urllib 一次性 read()。

    B站 CDN 大文件常在传输中途断连，urllib 的 resp.read() 会抛 IncompleteRead 且
    无续传；ffmpeg 自带重试 + 流式落盘， robustness 更好。UA/Referer 必须带，否则
    CDN 返回 403。逐个 CDN 尝试，全部失败才抛错。
    """
    headers = (f"User-Agent: {bilibili.UA}\r\n"
               f"Referer: {bilibili.WEB}/\r\n")
    last_err = None
    for url in filter(None, urls):
        try:
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-headers", headers, "-rw_timeout", "30000000",
                   "-i", url, "-c", "copy", "-f", "mp4", str(out_path)]  # -f mp4：缓存 .part 后缀需显式指定格式
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 1024:
                detail = (proc.stderr or "").strip().splitlines()
                raise CliError(f"ffmpeg 下载失败: {detail[-1][:160] if detail else '未知错误'}", 4)
            log(f"视频已下载 {out_path.stat().st_size / 1e6:.1f} MB")
            return
        except (CliError, subprocess.TimeoutExpired, OSError) as err:
            last_err = err
            log(f"该 CDN 失败（{err}），换下一个")
    raise last_err or CliError("视频轨没有可用下载地址", 3)


def fetch_bilibili_video(bvid, cid, work_dir, cookie=None, info=None,
                         reuse_cache=True, cache_base=None) -> Path:
    """B站 DASH 视频轨 → work_dir/<bvid>_<cid>.mp4（经缓存）。

    info 传入 dict 时记录所选流与体积（S6.3 资源画像）+ cache_hit。
    reuse_cache=True（默认）：命中缓存直接复用，跳过下载；False 强制重下并刷新缓存。
    cache_base：缓存根目录锚点（Agent 工作目录），缺省用 cwd。
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    data = bilibili.api_json(f"{audio.PLAYURL}?bvid={bvid}&cid={cid}&fnval=16", cookie=cookie)
    if data.get("code") != 0:
        raise CliError(f"取播放地址失败: {data.get('message')}", 3)
    videos = ((data.get("data") or {}).get("dash") or {}).get("video") or []
    track = pick_video_track(videos)
    log(f"视频轨: id={track.get('id')} bandwidth={track.get('bandwidth')} "
        f"codec={track.get('codecs')} ({track.get('width')}x{track.get('height')})")

    cache_file = _cache_path(_cache_key_bili(bvid, cid, track.get("id")), cache_base)
    out = work_dir / f"{bvid}_{cid}.mp4"

    if reuse_cache and _cache_hit(cache_file):
        log(f"复用视频缓存 {cache_file.name}（{cache_file.stat().st_size / 1e6:.1f} MB）")
        result = _link_from_cache(cache_file, out)
        hit = True
    else:
        tmp = cache_file.with_suffix(".part")
        _download_bilibili([track.get("baseUrl")] + list(track.get("backupUrl") or []), tmp)
        result = _finish_download(tmp, cache_file, out)
        hit = False

    if info is not None:
        info.update({
            "stream_id": track.get("id"),
            "codec": track.get("codecs"),
            "resolution": f"{track.get('width')}x{track.get('height')}",
            "bandwidth": track.get("bandwidth"),
            "video_mb": round(result.stat().st_size / 1e6, 1),
            "cache_hit": hit,
        })
    return result


def _remux_hls(url, out_path):
    """m3u8 → mp4（ffmpeg 直接读 HLS 播放列表，stream copy 不重编码）。"""
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", url, "-c", "copy", "-f", "mp4", str(out_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not out_path.exists():
        detail = result.stderr.decode("utf-8", "ignore").strip()[-200:]
        raise CliError(f"HLS 视频转存失败: {detail}", 4)


def fetch_xhs_video(note_id, urls, work_dir, reuse_cache=True, cache_base=None) -> Path:
    """小红书视频流 → work_dir/xhs_<note_id>.mp4（经缓存）。

    逐候选地址尝试（mp4 直下 / m3u8 转存）。reuse_cache=False 强制重下并刷新缓存。
    cache_base：缓存根目录锚点（Agent 工作目录），缺省用 cwd。
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    urls = [u for u in urls if u]
    cache_file = _cache_path(_cache_key_xhs(note_id, urls[0] if urls else ""), cache_base)
    out = work_dir / f"xhs_{note_id}.mp4"

    if reuse_cache and _cache_hit(cache_file):
        log(f"复用视频缓存 {cache_file.name}（{cache_file.stat().st_size / 1e6:.1f} MB）")
        return _link_from_cache(cache_file, out)

    tmp = cache_file.with_suffix(".part")
    last_err = None
    for url in urls:
        try:
            if url.split("?")[0].endswith(".m3u8"):
                _remux_hls(url, tmp)
            else:
                status, raw, _ = xiaohongshu._http_get(url)
                if status >= 400:
                    raise CliError(f"视频流下载失败（HTTP {status}）", 4)
                tmp.write_bytes(raw)
                log(f"视频已下载 {len(raw) / 1e6:.1f} MB")
            return _finish_download(tmp, cache_file, out)
        except CliError as err:
            last_err = err
            tmp.unlink(missing_ok=True)
            log(f"该视频地址失败（{err}），换下一个")
    raise last_err or CliError("没有可用视频流地址，无法提取画面", 3)
