# S7 交接文档 — 产品化输出冲刺（--type / 素材包 / get_assets / 重试 / 语言自动）

> 2026-11-22 · 用户拍板。前置：S6.5（1c9b7df）。本轮全部在 `tools/bili-digest/**`。

## A. `--type` 语义糖衣（用户词汇 → 管线行为）

```
digest <url> [--type talk|slides|mixed|auto]     # 默认 auto
```
| type | 语义（用户词汇） | 管线映射 |
|---|---|---|
| talk | 内容在说话（访谈/口播/旁白） | 不下载视频轨（等价现状"不加 --visual"），字幕/ASR → 报告 |
| slides | 内容在画面（PPT/图文卡/无声演示） | visual=ppt 策略 + **跳过 ASR**（transcript source 标 visual-only；用户要保险用 mixed） |
| mixed | 两边都要（稳妥） | visual=auto 策略 + **禁用口播门控**（双通道保证，同 ppt 禁门控的先例） |
| auto | 系统猜（默认） | 现状行为 |

- `--type` 与 `--visual` 同时出现 → 用法错误 exit 2（防歧义），文档推荐 `--type`。
- MCP `digest_video` 加 `type` 参数（enum，默认 auto）。
- `digest report`/其他子命令不受影响。

## B. 素材包（一等交付物）

- run 目录新增 **`assets.json`**：`{frames: [{file, t, frame_type, desc}], transcript: {files, segments, source, lang}, generated_at}`；**`assets.md`** 人读版：帧缩略图表格（时间/类型/描述首行）+ transcript 概要。
- `--no-report`：素材场景跳过 summarize（report.md 不生成）；默认仍生成（当素材目录页）。
- 既有产物（report/transcript.*/frames/enriched_transcript.md）全部保留不动。

## C. MCP 新工具（Agent 复用素材的闭环）

- `get_assets(job_id)` → assets.json 内容（帧清单+desc+transcript 概要）。
- `get_frame(job_id, file)` → 该帧图片 base64（多模态客户端可直接看图复用）。
- 工具 schema/测试风格照抄现有五工具；stdout 纪律不变。

## D. summarize 重试

- 网络类错误（读超时/连接重置）退避一次（30s）再试；仍失败按现行 CliError 语义。单测 mock 超时→重试成功/双败。

## E. 语言自动检测

- `--lang` 默认从 `zh` 改为自动：mlx-whisper 不传 language（其自带检测）；云端 SenseVoice 多语种本就自动；字幕路径不受影响。`--lang` 显式指定时行为不变。
- 离线验收：BV1xaWH6PEnQ 的本地音频重转 → 检测 en；任一中文样本 → zh。

## 验收标准（逐条证据）

1. `--type` 四路路由 + 与 `--visual` 冲突退出码：单测；slides 跳 ASR 在一条本地样本离线实测。
2. assets.json/md 结构正确 + `--no-report` 生效：离线样本实测。
3. MCP `get_assets`/`get_frame` 子进程 JSON-RPC 全链路单测（七工具总数同步断言）。
4. summarize 重试单测通过。
5. 语言自动：两语种离线实测正确。
6. pytest 全绿（301+ 新增）；README 更新（--type 表/素材包/新 MCP 工具）；真实请求 ≤2、LLM/VLM <¥0.3。

## 边界（硬性）
- 只动 `tools/bili-digest/**`；不执行 git；零新依赖；key 不打印。
- 优先离线复用 runs/ 既有媒体文件（BV1xaWH6PEnQ 音视频、中文样本视频均在）。
