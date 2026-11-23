"""enrich 组件（S6 视觉增强）：关键帧提取 → VLM 读图 → 时间轴融合。

- frames：ffmpeg 场景检测取帧（本地确定性，零网络）
- vlm：智谱 GLM-4V 读图（stdlib urllib，带笔记标题上下文）
- fusion：transcript 段 + 帧描述按时间戳合成富化时间轴
- video：视频流下载（B站 DASH video / 小红书 mp4 直链，--visual 前置）
"""

from . import frames, fusion, video, vlm

__all__ = ["frames", "fusion", "video", "vlm"]
