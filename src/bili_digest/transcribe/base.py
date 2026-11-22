"""Transcriber 协议与 Transcript 数据结构（方案设计 §2 冻结接口）。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# 无语音/幻觉循环启发式阈值（纯 BGM 音频上 whisper 常产出同句文本循环，2026-11-21 实测）
SILENCE_REPEAT_RUN = 3      # ① 相邻段文本完全相同的连续重复 ≥3 段
SILENCE_UNIQUE_RATIO = 0.2  # ② 全部段落去重后唯一文本占比 < 20%


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    segments: list[Segment]
    lang: str
    engine: str  # "mlx-whisper" / "siliconflow-sensevoice" / "subtitle"
    source: str  # "subtitle" | "asr"
    elapsed_s: float
    suspected_silent: bool = False  # 命中无语音启发式，转写内容可能是幻觉


def suspects_silence(segments) -> bool:
    """通用无语音启发式（引擎无关，无概率字段的云端 SenseVoice 也生效）。"""
    texts = [s.text.strip() for s in segments]
    if not texts:
        return False
    run = longest = 1
    for prev, cur in zip(texts, texts[1:]):
        run = run + 1 if cur and cur == prev else 1
        longest = max(longest, run)
    if longest >= SILENCE_REPEAT_RUN:
        return True
    return len(set(texts)) < len(texts) * SILENCE_UNIQUE_RATIO


@runtime_checkable
class Transcriber(Protocol):
    name: str

    def available(self) -> bool:
        """依赖 + 平台自检（mlx 需 arm64）。"""
        ...

    def transcribe(self, wav: Path, lang: str = "zh") -> Transcript:
        ...
