#!/usr/bin/env python3
"""sherpa-onnx SenseVoice 转写（CPU，AS/Intel 通用）。用法: run_sense.py <wav> <model_dir> <out.txt>"""
import re
import sys
import time
import wave

import numpy as np
import sherpa_onnx


def main():
    wav, model_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
    rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=f"{model_dir}/model.int8.onnx",
        tokens=f"{model_dir}/tokens.txt",
        use_itn=True,
        language="zh",
    )
    # sherpa-onnx 2.0.2 无 read_wave，用标准库读 16k mono s16 wav
    with wave.open(wav, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    stream = rec.create_stream()
    stream.accept_waveform(16000, samples)
    t0 = time.time()
    rec.decode_stream(stream)
    text = re.sub(r"<\|[^|]*\|>", "", stream.result.text)
    open(out, "w").write(text.strip())
    print(f"耗时 {time.time() - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
