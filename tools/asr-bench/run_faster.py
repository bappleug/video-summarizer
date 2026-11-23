#!/usr/bin/env python3
"""faster-whisper CPU 转写（AS/Intel 通用）。用法: run_faster.py <wav> <out.txt> [model] [compute_type]"""
import sys
import time

from faster_whisper import WhisperModel


def main():
    wav, out = sys.argv[1], sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else "large-v3-turbo"
    compute = sys.argv[4] if len(sys.argv) > 4 else "int8"
    m = WhisperModel(model, device="cpu", compute_type=compute)
    t0 = time.time()
    segments, _info = m.transcribe(wav, language="zh", vad_filter=True)
    text = "".join(s.text for s in segments)
    open(out, "w").write(text.strip())
    print(f"耗时 {time.time() - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
