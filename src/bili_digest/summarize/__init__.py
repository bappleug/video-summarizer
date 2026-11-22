"""summarize 组件：文字 → 报告（方案设计 §0，独立总结技能，可脱离壳工程使用）。

对外接口：
- summarize_text(text, meta=None, provider="zhipu") —— 任意文本 + 可选元信息 → Markdown 报告
- chat / PROVIDERS / estimate_cost —— OpenAI 兼容 chat client 与费率
"""

from .llm import PROVIDERS, chat, estimate_cost
from .report import summarize_text, summarize_transcript

__all__ = ["summarize_text", "summarize_transcript", "chat", "PROVIDERS", "estimate_cost"]
