# S4 交接文档 — bili-digest 小红书支持

> Phase 3 / S4 · 依据 [方案设计 v1.2](../方案设计.md) §1/§4/§6 与 research/02 §3b。本文档自包含。

## 目标

`tools/bili-digest/` 支持小红书**视频笔记**：`digest <小红书链接>` 出 transcript（内嵌字幕优先 → 回退 ASR），`--summarize` 可出报告。走**网页版**（解析页面数据，无官方 API）。

## 路线（已定，勿改）

- 抽取 `vendors/video-report-agent` 的小红书取材模块（约 165 行，MIT，commit af73abd；research/02 §3b：读页面 `__INITIAL_STATE__` 的 note 与 mediaV2，提视频流与内嵌字幕，保留 xsec_token，**匿名优先、401/403 才带 cookie 重试一次**，含 xhslink 短链安全重定向）。**复制+署名**到 `platforms/xiaohongshu.py`，适配本包接口风格（对齐 bilibili.py 的函数形态与错误语义）。
- cookie：可选 `XHS_COOKIE`（写进 keys.env，config.py 解析；README 说明从浏览器复制的路径）。匿名能用就不要求配置。
- 管线接入：`pipeline.py` 的 URL 路由（B站 BV/链接 | 小红书 xhslink/xiaohongshu.com | 分发到对应 platform）；内嵌字幕 → **复用 validate 四规则**（标题相关性对笔记标题同样适用）；无字幕 → 视频流地址 → audio.py 下载（必要时扩展：xhs 视频可能是直链 mp4 或分段流，ffmpeg 处理）→ transcribe 组件 → transcript。产物目录 `runs/<note-id>/<时间戳>/`。

## 前置事实

- S1-S3 已入库（最新 dfc4476）：`platforms/bilibili.py`（接口范本）、`audio.py`、`transcribe/`、`validate.py`（四规则）、`summarize/`、`pipeline.py`、`cli.py`，119 单测全绿。venv 复用，装包 `UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`。
- `vendors/video-report-agent` 只读；抽取时保留原版权注释 + 来源 commit。
- **零新增第三方依赖**（stdlib only；网页解析若 VRA 原实现用了第三方库，改用 stdlib html/json 正则等价实现，如实记录改造点）。
- 小红书反爬比 B站凶：**真实请求全天 ≤6 次**，每次间隔 ≥30s；xsec_token 缺失的裸链接可能 403，属预期，如实记录即不算失败。

## 验收标准（逐条给证据）

1. URL 路由单测：bilibili 链接/BV号/xhslink 短链/xiaohongshu.com 长链/未知 URL 各归各类。
2. **真实 E2E ≥1 条**：公开视频笔记 → transcript 落盘（来源：自己找的公开链接，或 runs/ 里用户提供的）。若全部被反爬阻断：如实记录阻断形态（403/验证页/xsec_token 要求），并给出「需要用户提供链接或 cookie」的明确结论——这本身是有效交付。
3. 内嵌字幕路径：若该笔记带字幕则走字幕 + validate；无字幕走 ASR。两条路径至少各验一条（第二条可 mock）。
4. `--summarize` 对小红书笔记出报告（元信息用笔记标题/作者）。
5. `pytest` 全绿（119+ 新增）；README 增小红书节（用法/cookie 配置/已知限制）。
6. LLM 花费 <¥0.1。

## 边界（硬性）

- 只动 `tools/bili-digest/**`；vendors 只读；不碰 docs/AGENTS.md；**不执行 git**。
- key/cookie 纪律同前（不打印不入库）。
- 完成报告：实现概览、验收逐条证据、VRA 抽件的改造点清单、新装依赖（应为零）、遇到的坑。
