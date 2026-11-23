"""mlx-whisper 转写后端（仅 Apple Silicon）。

转写调用复制自 scripts/asr-bench/run_mlx.py，对齐 asr.base.Transcriber 协议。
mlx 相关 import 延迟到方法内，保证 core 零第三方依赖。
"""

import platform
import time
from pathlib import Path

from .. import config
from ..errors import CliError, log
from .base import Segment, Transcript, suspects_silence

# whisper 官方惯例：no_speech_prob 超阈值的段视为无语音，丢弃
NO_SPEECH_PROB_MAX = 0.6


class MlxTranscriber:
    name = "mlx-whisper"

    def available(self) -> bool:
        if platform.machine() != "arm64":
            return False
        try:
            import mlx_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe(self, wav: Path, lang=None) -> Transcript:
        try:
            import mlx_whisper
        except ImportError:
            raise CliError("mlx-whisper 未安装：uv pip install -e '.[mlx]'", 3)
        t0 = time.time()
        kwargs = {"path_or_hf_repo": config.MLX_MODEL}
        if lang:  # S7：默认自动检测（不传 language，mlx-whisper 自带检测）
            kwargs["language"] = lang
        result = mlx_whisper.transcribe(str(wav), **kwargs)
        detected = result.get("language") or lang or "auto"
        segments, dropped = [], 0
        for s in result.get("segments") or []:
            if float(s.get("no_speech_prob") or 0.0) > NO_SPEECH_PROB_MAX:
                dropped += 1
                continue
            segments.append(Segment(start=float(s.get("start", 0.0)),
                                    end=float(s.get("end", 0.0)),
                                    text=str(s.get("text", "")).strip()))
        if dropped:
            log(f"丢弃 {dropped} 段疑似无语音（no_speech_prob > {NO_SPEECH_PROB_MAX}）")
        return Transcript(
            segments=segments, lang=detected, engine=self.name, source="asr",
            elapsed_s=time.time() - t0, suspected_silent=suspects_silence(segments),
        )
