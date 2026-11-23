# S3 交接文档 — bili-digest MCP Server

> Phase 3 / S3 · 依据 [方案设计 v1.2](../方案设计.md) §6 与 D-002（CLI+MCP 双形态）。本文档自包含。

## 目标

给 `tools/bili-digest/` 增加 MCP stdio server，让豆包等支持 MCP 的客户端能「给链接、拿 transcript/报告」。**核心管线零改动**（S1/S2 已验收），只做服务化包装。

## 前置事实

- S2 已入库（commit 64931e7）：`pipeline.py`（同步编排）、`cli.py`、`doctor.py`、`transcribe/`、`summarize/`，90 单测全绿。venv 复用（装包 `UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`）。
- MCP 工具设计蓝本：`vendors/bili-transcriber` 的 `app/mcp_server.py`（**只读参考，不抄依赖**；research/03 §3c 有抄录）——重点看它的 stdout 污染防护、参数 clamp、空 resources/prompts 兼容、NaN 净化。
- 豆包等真实客户端对协议细节敏感：**允许引入官方 `mcp` Python SDK**（唯一例外于零依赖纪律，作为可选 extra `[mcp]`；核心管线保持 stdlib）。装完报台账。
- B站真实 API 今日已消耗较多（风控活跃），**单测一律 mock pipeline**；真实端到端最多 1 次（P5）。

## 工作项

### 1. `mcp_server.py` + job 管理
- 5 工具（冻结命名）：
  - `digest_video(url, summarize=False, engine="auto", page=None, format="txt")` → 提交后台线程 job，返回 `{job_id, status}`
  - `job_status(job_id)` → `{status: queued|running|done|error, progress: 阶段描述, error?: 修法指引}`
  - `get_transcript(job_id, format="txt")` → 文本内容（done 后可用）
  - `get_report(job_id)` → 报告 Markdown（--summarize 的 job 才有）
  - `cancel(job_id)` → 取消（running 中协作取消点在阶段边界）
- job 管理：进程内（dict + threading），S3 不做持久化（S5 的事）；job_id 用短 uuid。
- 错误透传：pipeline 的 CliError（含 key/登录修法指引）原样进 job error 字段。

### 2. 协议与工程细节（照抄 BTC 的好习惯）
- **stdout 只有 MCP 协议帧**：一切日志/进度走 stderr（否则客户端解析直接崩）。
- 参数防御：url 长度/类型 clamp；非法参数返回结构化错误而非异常崩溃。
- initialize 响应带 `capabilities: {tools: {listChanged: false}}`，`resources/prompts` 返回空列表（兼容严格客户端）。

### 3. CLI 入口 + 文档
- `digest mcp`（stdio 模式启动）。
- README 增「MCP 接入」节：通用 JSON 配置示例（command=venv 内 digest、args=["mcp"]）+ 豆包类客户端配置说明（截图级步骤不需要，配置片段即可）。
- pyproject 加 `[mcp]` extra。

## 验收标准（逐条给证据）

1. 用子进程 JSON-RPC 模拟客户端：initialize 握手 → `tools/list` 返回 5 工具及正确 schema → `tools/call digest_video`（mock pipeline，立即 done）→ `get_transcript` 拿到内容——全链路单测覆盖。
2. 真实端到端 1 次（可选，若 B站风控允许）：MCP 提交 P5 → 轮询 done → get_transcript 内容正确；若当日风控不放行，用 mock 全链路 + 如实记录。
3. stdout 纯净性测试：跑一轮完整交互后 stdout 逐行可 JSON parse。
4. 并发：两个 job 同时 running 互不干扰（mock 慢 pipeline 单测）。
5. `pytest` 全绿（90+ 新增）；`digest mcp` 冒烟启动不崩。
6. README MCP 节完整（配置片段可直接复制）。

## 边界（硬性）

- 只动 `tools/bili-digest/**`；不碰 vendors/docs/其他 tools；**不执行 git**。
- 新增依赖仅 `mcp` SDK 一个（进 `[mcp]` extra + 台账报告）；其余 stdlib。
- B站真实 API ≤1 次；key/凭据纪律同前。
