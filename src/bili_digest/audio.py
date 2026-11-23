"""DASH 音频下载 + ffmpeg 转 16k 单声道 wav（ASR 前置）。

下载逻辑复制自 scripts/asr-bench/fetch_audio.py，改造点：
- 音轨选择独立成 pick_audio_track()（最大带宽优先，缺失带宽按 0 处理）
- m4a 走临时文件，转码成功后清理
- ffmpeg 转码校验（退出码 + 产物存在且大于 wav 头）
- 复用 platforms/bilibili 的 http/api_json（412 风控语义一致）
playurl 接口免登录；CDN 需要 UA + Referer。依赖系统 ffmpeg。
"""

import os
import subprocess
import tempfile
from pathlib import Path

from .errors import CliError, log
from .platforms import bilibili

PLAYURL = f"{bilibili.API}/x/player/playurl"


def pick_audio_track(audios):
    """从 DASH 音轨列表选最大带宽的一条。"""
    if not audios:
        raise CliError("该视频没有 DASH 音轨，无法走本地 ASR", 3)
    return max(audios, key=lambda a: a.get("bandwidth") or 0)


def download_file(url, path):
    status, raw, _ = bilibili.http(url, referer=bilibili.WEB + "/")
    if status >= 400:
        raise CliError(f"音频下载失败（HTTP {status}）", 4)
    path.write_bytes(raw)
    log(f"音频已下载 {len(raw) / 1e6:.1f} MB")


def to_wav16k(m4a, wav):
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(m4a),
         "-ac", "1", "-ar", "16000", str(wav)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not wav.exists() or wav.stat().st_size <= 44:
        detail = result.stderr.decode("utf-8", "ignore").strip()[-300:]
        raise CliError(f"ffmpeg 转码失败: {detail}", 1)


def fetch_audio(bvid, cid, work_dir, cookie=None):
    """下载整段音频并转为 16k 单声道 wav，返回 wav 路径。m4a 中间文件用完即删。"""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    data = bilibili.api_json(f"{PLAYURL}?bvid={bvid}&cid={cid}&fnval=16", cookie=cookie)
    if data.get("code") != 0:
        raise CliError(f"取播放地址失败: {data.get('message')}", 3)
    audios = ((data.get("data") or {}).get("dash") or {}).get("audio") or []
    track = pick_audio_track(audios)
    log(f"音轨: id={track.get('id')} bandwidth={track.get('bandwidth')} codec={track.get('codecs')}")

    fd, tmp = tempfile.mkstemp(dir=work_dir, suffix=".m4a")
    os.close(fd)
    m4a = Path(tmp)
    wav = work_dir / f"audio_{bvid}_{cid}.wav"
    try:
        urls = [track.get("baseUrl")] + list(track.get("backupUrl") or [])
        last_err = None
        for url in filter(None, urls):
            try:
                download_file(url, m4a)
                last_err = None
                break
            except CliError as err:
                last_err = err
                log(f"该 CDN 失败（{err}），换下一个")
        if last_err is not None:
            raise last_err
        to_wav16k(m4a, wav)
    finally:
        m4a.unlink(missing_ok=True)
    return wav
