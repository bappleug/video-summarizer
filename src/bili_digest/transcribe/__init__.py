"""transcribe 组件：音视频 → 文字（方案设计 §0，可脱离壳工程独立使用）。

对外接口：Segment / Transcript / Transcriber 协议 + mlx / cloud 两个后端。
输入是 wav 路径，不依赖 platforms / pipeline。
"""

from .base import Segment, Transcript, Transcriber
from .cloud_sf import CloudSfTranscriber
from .mlx_backend import MlxTranscriber

__all__ = ["Segment", "Transcript", "Transcriber", "MlxTranscriber", "CloudSfTranscriber"]
