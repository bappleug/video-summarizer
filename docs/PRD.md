# PRD — B站视频内容提取与总结工具

> 状态：**v1.1** — 2026-11-20 基于 Phase 1 调研结论与用户决策（D-006/D-007）定稿 v1.0；2026-11-23 现状对齐：标注已实现 / 未实现 / 被决策取代的需求项，需求本身不变
> 维护：随决策更新，重大变更记录到 [进度记录](进度记录.md)。

## 1. 背景与目标

需要把 B站（兼顾小红书）视频快速变成「可读的文字」。B站视频分两类：

- **有字幕**（UP 主人工或 AI 字幕）：直接抓字幕轨即可，快且零成本。但 B站字幕接口**要求登录态**，且 cookie 会过期，这是已有工具普遍没解决好的难点。
- **无字幕**：需要下载音频并做语音识别（ASR）。

目标：形成一条**本地优先、低使用门槛**的管线，transcript 是核心产出，总结报告是增值产出。

## 2. 用户与场景

| 用户 | 场景 | 对应形态 |
|---|---|---|
| 开发者（我们自己） | 命令行里丢一个链接拿 transcript / 报告 | CLI（MVP） |
| AI Agent 用户 | 在豆包等支持 MCP 的本地大模型 APP 里说「总结这个视频」 | MCP Server（已交付：9 工具；Work Buddy 已联调通过，豆包客户端未联调） |
| 非程序员 | 打开网页/本地应用贴链接出结果 | 本地 Web UI / 桌面应用（Phase 4，未排期）——现阶段经 AI Agent 宿主使用 |

关键约束：**最终用户里有多人共用一批 Mac（Apple Silicon 与老 Intel 混合）**。本地 ASR 仅支持 Apple Silicon；Intel 及其他设备一律走云端 API（D-008，云端已实测免费可用）。

## 3. 核心管线

```
视频 URL
  → 元信息（标题/UP/时长/分P）
  → [字幕优先] 有字幕轨 → 直接得到 transcript        ← tools/bili-subtitle（已有）
  → [ASR 回退] 无字幕 → 下载 DASH 音频 → ASR（云端硅基流动保底；Apple Silicon 可选本地 mlx，D-010）→ transcript
  → [增值] LLM 总结 → 结构化报告（Markdown；HTML 未实现）
```

## 4. 功能需求与优先级

| 优先级 | 功能 | 说明 |
|---|---|---|
| P0 | B站 transcript 管线 | 字幕优先 + ASR 回退；输出 txt/srt/md/json。现状：已实现（多 P 支持；合集 season 处理未实现） |
| P0 | ASR：AS 本地 mlx + 其他设备云端 | D-008：本地仅 Apple Silicon（mlx-whisper）；Intel 等走硅基流动（免费）。现状：已实现——云端为保底主路线、本地 mlx 为 `[mlx]` 可选插件（D-010），`--engine auto / mlx / cloud` |
| P1 | 总结/报告生成 | 可配置在线 LLM API；总结可上云（内容为公开视频）。现状：已实现——默认硅基流动（托管 DeepSeek-V4.1-Flash），zhipu / DeepSeek 官方 key 为可选备用（`SUMMARIZE_PROVIDER` 覆盖）；不自动跨供应商兜底，失败时交宿主接管（S7.5）；报告仅 Markdown |
| P1 | 小红书支持 | 视频笔记的 transcript 提取。现状：已实现（S4；需 `XHS_COOKIE`，可由 Chrome 插件 cookie-exporter 导出） |
| P2 | MCP Server | 统一工具接口，供豆包等 Agent APP 调用；架构上 CLI 与 MCP 共享同一核心库。现状：已实现——9 个工具；Work Buddy 已联调通过，豆包客户端未联调 |
| P2 | 云 ASR（Intel 设备主路线） | 硅基流动 SenseVoice（免费、本地文件直传）。现状：已实现并实测免费；D-010 后为保底主路线（不再是「降级」） |
| P3 | 批量任务 / 历史检索 | 多链接队列、转写历史全文检索。现状：未实现（S5 未做；MCP job 仅进程内、不持久化） |
| P2（S6，MVP 后） | 视觉增强 | 面向画面信息量与语音等价的视频（讲义/教程类）：关键帧提取（场景切换检测 + pHash 去重 + 上限）→ VLM 画面解读（硅基流动 Qwen3-VL-8B-Instruct）→ 与 transcript 统一时间轴融合 → 报告嵌入关键帧。管线中 transcribe 与 summarize 之间的可选 `enrich` 组件。现状：已实现（S6–S6.5，经 `--visual[=auto|ppt]` / `--type talk|slides|mixed|auto` 使用） |

## 5. 非功能需求

- **引擎策略（原「本地优先」，D-010 后反转）**：云端硅基流动为保底主路线；本地 mlx 为可选插件——装了 `[mlx]` 的 Apple Silicon 上 ASR 在本地完成、音频不出机器（「零 key 可用」仅在此成立）。
- **设备兼容**：Apple Silicon 与 Intel Mac 双支持；模型/引擎按设备能力自动或配置选择。
- **登录门槛**：B站字幕依赖登录态：扫码登录（`credentials.json`，优先）或 Chrome 插件导出 `BILI_COOKIE`。cookie 自动续期已失效（B站改机制），按 D-012 不做逆向修复，改为「登录态失效提醒」——`digest doctor` / `digest status` 实测并如实报告登录态、给出重新扫码或插件导出指引；字幕路线失效时管线自动走 ASR，结果不受影响。
- **成本可控**：见 §6。
- **零依赖偏好**：核心链路尽量少引重依赖（bili-subtitle 是范本）；ASR 引擎按需安装为可选组件。

## 6. 成本预估（2026-11-20 行情，仅供量级参考）

**关键事实：云端 ASR 是独立产品线，按音频时长计费，与对话模型的 token 费率无关，价格很低。**

| 服务 | 价格（折合） | 备注 |
|---|---|---|
| 阿里 paraformer-v2 录音识别 | ¥0.29/小时 | 每月送 10 小时免费 |
| 阿里百炼 qwen-audio filetrans | ¥0.79/小时 | video-report-agent 默认用的这类 |
| 火山豆包录音文件识别（标准版） | ~¥1/小时 | 有免费试用额度 |
| OpenAI whisper-1 | $0.36/小时 | |
| Groq Whisper large-v3-turbo | ~$0.04/小时 | 最便宜，海外网络 |
| 硅基流动 SenseVoice-Small | 极低/曾有免费档 | 中文效果好，申请门槛低 |

结论：**云 ASR 兜底成本 ≈ ¥0.3~1.5/小时视频**，量级上不构成负担；硅基流动 SenseVoice 实测免费（research/04），D-010 后反转为保底主路线，本地 mlx 降为可选插件（装了的 Apple Silicon 上零边际成本、无网络依赖）。总结环节 LLM 的成本取决于报告长度（几千 token/篇），用主流 API 每篇约几分钱，可忽略。

## 7. 技术路线（v1.0 已定案：自研整合，D-007）

**架构 = 以 `tools/bili-subtitle` 为核心的自研管线，嵌入两个开源项目的抽取组件：**

```
URL → 平台识别（B站 / 小红书 / 本地文件）
  → B站：bili-subtitle 抓字幕（登录态+内容校验*）                 [自研，已有]
  → 小红书：取材模块（解析页面 __INITIAL_STATE__，匿名优先）      [抽 VRA 改造，stdlib 自实现]
  → 无字幕回退：DASH 音频下载 → ffmpeg 16k → ASR 引擎抽象层      [自研 + 抽 BTC 接口形状]
       ├─ 装了 [mlx] 的 Apple Silicon → mlx-whisper（本地，可选插件）[D-006/D-008/D-010]
       └─ 其余情况（保底主路线）→ 硅基流动 SenseVoice API（云端，实测免费，.cn 端点）[D-008/D-010, research/04]
  → transcript（txt/srt/md/json）
  → 总结报告：可配置 LLM（默认硅基流动托管 DeepSeek-V4.1-Flash） [自研，模板参考 VRA Skill]
  → 交付形态：CLI + MCP Server（共享核心库，MCP 设计参考 BTC）    [D-002]
```

\* 字幕内容校验为必选组件（Phase 1 发现 B站字幕风控污染，research/02 亦证实「时长合法但内容无关」的污染挡不住）。

**抽件清单**（详见 research/02、03）：

| 来源 | 抽取组件 | 用途 |
|---|---|---|
| video-report-agent | 小红书取材模块（165 行）、B站 ai-zh protobuf 解析 + validate_cues、报告 Skill 模板 | 小红书支持 / 字幕链路增强 / 总结模板 |
| bili-transcriber | MCP 11 工具设计、SenseVoice 多线程封装、三引擎适配器接口形状、短链安全重定向 | Phase 3 MCP 蓝本 / ASR 提速 / 引擎抽象 |

现状（2026-11-23）：抽件清单中 **ai-zh protobuf 解析**与 **BTC 本地 SenseVoice 封装**最终未采用（后者随 D-008 退出）；**短链处理**以更简方式覆盖；其余组件均已抽取落地。

**登录态结论（D-012，2026-11-23）**：cookie 自动续期确认失效（B站改机制），不做逆向修复，改为「登录态失效提醒」——`digest doctor` / `digest status` 实测报告登录态并给出重新扫码或插件导出的指引；字幕路线失效时管线自动走 ASR，结果不受影响。

## 8. 里程碑

- **Phase 0**（✅ 2026-11-20 完成）：工作区搭建。
- **Phase 1**（✅ 2026-11-20 完成）：调研实测（清单见进度记录），产出评估报告与「用/改/自研」结论。
- **Phase 2**（✅ 2026-11-20 完成）：PRD 定稿 + 架构定案。
- **Phase 3**（✅ 已完成并发布，产品 v1.2.0）：MVP——transcript 管线 CLI + MCP Server（S1–S4、S6 含 S6.1–S6.5、S7 含 S7.2–S7.6、S8 完成；发布仓 github.com/bappleug/video-summarizer）。
- **Phase 4**（未启动）：非程序员界面、批量、小红书完善——其中小红书支持已在 S4 完成；**S5（SQLite 历史 + 批量队列）未做**。

## 9. 决策状态（v1.1）

已定：D-001~D-014（见进度记录）。最新：
- **D-008 放弃 Intel 本地 ASR**（AS=本地 mlx；其余设备=云端硅基流动）✅ 2026-11-20
- **D-009 Key 体验一等需求**（doctor 体检 + 错误分类指引）✅ 2026-11-20
- **D-010 云端为保底主路线，本地 mlx 降可选插件** ✅ 2026-11-20
- **D-011 三组件化**（transcribe / summarize / 壳工程）✅ 2026-11-20
- **D-012 B站 cookie 自动续期不做逆向修复，转「登录态失效提醒」**（doctor / status 实测报告登录态；字幕路线失效时自动走 ASR）✅ 2026-11-23
- **D-013 git 仓库根 = `video-parser/` 工作区整体入库；`ai-tools/` 仅为本地容器，不建仓** ✅ 2026-11-23
- **D-014 工程内不得写死 clone 位置；运行时配置目录保持 home 相对路径（`~/.config/ai-tools/…`）** ✅ 2026-11-23

仍开放（Phase 2/3 中收敛）→ 均已收敛：
- 总结 LLM 默认供应商最终确认 → 已定：默认硅基流动（托管 DeepSeek-V4.1-Flash），zhipu / DeepSeek 官方 key 为可选备用（S7.3）。
- 云 ASR 降级是否进 MVP 范围 → 已进：硅基流动云转写进 S2、实测免费，并按 D-010 升为保底主路线。
- 报告模板形态 → 已定：仅 Markdown（HTML 报告未实现）。
- bili-subtitle cookie 续期机制修复方案 → 已定：不逆向修复，转「登录态失效提醒」（D-012）。
