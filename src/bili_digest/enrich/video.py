"""视频流下载（--visual 前置）：B站 DASH video 轨 / 小红书 mp4 直链 → run 目录 download/。

与 audio.py 分离（S 交接文档 §4：video 下载与 audio 下载分开，audio.py 保持不动）：
- B站：playurl fnval=16 → dash.video 选轨（S6.3：≤720p 内优先 h264 取最大带宽——读图
  足够、长视频下载体积可控；无 ≤720p 轨退回全列表最大带宽），
  复用 platforms/bilibili.http 下载（UA + Referer，412 风控语义一致）；
- 小红书：masterUrl/backupUrls 直链（h264 优先已在 note_info 排好），
  复用 platforms/xiaohongshu._http_get；m3u8 地址交给 ffmpeg 转存 mp4。
"""

import subprocess
from pathlib import Path

from .. import audio
from ..errors import CliError, log
from ..platforms import bilibili, xiaohongshu

MAX_STREAM_HEIGHT = 720  # 选轨清晰度上限（读图足够，56min 级视频体积可控）


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
    last_err = None
    for url in filter(None, urls):
        try:
            status, raw, _ = bilibili.http(url, referer=bilibili.WEB + "/")
            if status >= 400:
                raise CliError(f"视频下载失败（HTTP {status}）", 4)
            out_path.write_bytes(raw)
            log(f"视频已下载 {len(raw) / 1e6:.1f} MB")
            return
        except CliError as err:
            last_err = err
            log(f"该 CDN 失败（{err}），换下一个")
    raise last_err or CliError("视频轨没有可用下载地址", 3)


def fetch_bilibili_video(bvid, cid, work_dir, cookie=None, info=None) -> Path:
    """B站 DASH 视频轨 → work_dir/<bvid>_<cid>.mp4。info 传入 dict 时记录所选流与体积
    （S6.3 资源画像：stream_id/codec/resolution/bandwidth/video_mb → meta）。"""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    data = bilibili.api_json(f"{audio.PLAYURL}?bvid={bvid}&cid={cid}&fnval=16", cookie=cookie)
    if data.get("code") != 0:
        raise CliError(f"取播放地址失败: {data.get('message')}", 3)
    videos = ((data.get("data") or {}).get("dash") or {}).get("video") or []
    track = pick_video_track(videos)
    log(f"视频轨: id={track.get('id')} bandwidth={track.get('bandwidth')} "
        f"codec={track.get('codecs')} ({track.get('width')}x{track.get('height')})")
    out = work_dir / f"{bvid}_{cid}.mp4"
    _download_bilibili([track.get("baseUrl")] + list(track.get("backupUrl") or []), out)
    if info is not None:
        info.update({
            "stream_id": track.get("id"),
            "codec": track.get("codecs"),
            "resolution": f"{track.get('width')}x{track.get('height')}",
            "bandwidth": track.get("bandwidth"),
            "video_mb": round(out.stat().st_size / 1e6, 1),
        })
    return out


def _remux_hls(url, out_path):
    """m3u8 → mp4（ffmpeg 直接读 HLS 播放列表，stream copy 不重编码）。"""
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", url, "-c", "copy", str(out_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not out_path.exists():
        detail = result.stderr.decode("utf-8", "ignore").strip()[-200:]
        raise CliError(f"HLS 视频转存失败: {detail}", 4)


def fetch_xhs_video(note_id, urls, work_dir) -> Path:
    """小红书视频流 → work_dir/xhs_<note_id>.mp4。逐候选地址尝试（mp4 直下 / m3u8 转存）。"""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / f"xhs_{note_id}.mp4"
    last_err = None
    for url in filter(None, urls):
        try:
            if url.split("?")[0].endswith(".m3u8"):
                _remux_hls(url, out)
            else:
                status, raw, _ = xiaohongshu._http_get(url)
                if status >= 400:
                    raise CliError(f"视频流下载失败（HTTP {status}）", 4)
                out.write_bytes(raw)
                log(f"视频已下载 {len(raw) / 1e6:.1f} MB")
            return out
        except CliError as err:
            last_err = err
            out.unlink(missing_ok=True)
            log(f"该视频地址失败（{err}），换下一个")
    raise last_err or CliError("没有可用视频流地址，无法提取画面", 3)
