#!/usr/bin/env python3
"""下载 B站 DASH 音频并切成 16k 单声道 wav（ASR 基准测试素材）。

用法: python3 fetch_audio.py <BV号> <cid> <输出目录> [--start 秒] [--dur 秒]

playurl 接口免登录；CDN 需要 UA + Referer。依赖系统 ffmpeg。
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
API = "https://api.bilibili.com/x/player/playurl"


def get_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def download(url, path, chunk=1 << 20):
    req = urllib.request.Request(url, headers=HEADERS)
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            b = r.read(chunk)
            if not b:
                break
            f.write(b)
            done += len(b)
            if total:
                print(f"\r下载 {done/1e6:.1f}/{total/1e6:.1f} MB", end="", flush=True,
                      file=sys.stderr)
    print(file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bvid")
    ap.add_argument("cid")
    ap.add_argument("outdir")
    ap.add_argument("--start", type=int, default=600, help="切片起点秒")
    ap.add_argument("--dur", type=int, default=300, help="切片时长秒")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    data = get_json(f"{API}?bvid={args.bvid}&cid={args.cid}&fnval=16")["data"]
    audio = max(data["dash"]["audio"], key=lambda a: a.get("bandwidth", 0))
    url = audio["baseUrl"]
    print(f"音轨: id={audio['id']} bandwidth={audio.get('bandwidth')} codec={audio.get('codecs')}",
          file=sys.stderr)

    m4a = os.path.join(args.outdir, "audio.m4a")
    if not os.path.exists(m4a):
        download(url, m4a)

    wav = os.path.join(args.outdir, f"slice_{args.start}_{args.dur}.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(args.start), "-t", str(args.dur),
         "-i", m4a, "-ac", "1", "-ar", "16000", wav],
        check=True,
    )
    print(wav)


if __name__ == "__main__":
    main()
