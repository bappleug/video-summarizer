#!/usr/bin/env python3
"""mlx-whisper 转写（仅 Apple Silicon）。用法: run_mlx.py <wav> <out.txt> [model]"""
import sys
import time

import mlx_whisper


def main():
    wav, out = sys.argv[1], sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else "mlx-community/whisper-large-v3-turbo"
    t0 = time.time()
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=model, language="zh")
    text = "".join(seg.get("text", "") for seg in r.get("segments", []))
    open(out, "w").write(text.strip())
    print(f"耗时 {time.time() - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
