# bili — 视频内容提取与总结工作区

围绕**哔哩哔哩**（兼顾小红书等平台）视频，形成一套本地优先的内容提取与总结能力：

```
视频 URL → 字幕抓取（优先） → 无字幕时下载音频 + 本地 ASR → transcript →（可选）LLM 总结报告
```

核心原则：**ASR 本地小模型优先**（兼容 Apple Silicon 与 Intel 两代 Mac），总结环节可用可配置的在线 API；最终通过 CLI 与 MCP Server 双形态交付，供 AI Agent 客户端（豆包等）和后续的图形界面复用。

## 当前状态

**Phase 1（调研实测）已完成**，PRD 定稿 v1.0（D-006 双引擎 ASR / D-007 自研整合已拍板），进入 Phase 2（架构与组件抽取方案）。详见 [进度记录](docs/进度记录.md)。

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | 工作区搭建：目录重构、文档骨架、vendor 克隆 | ✅ 2026-11-20 |
| Phase 1 | 调研实测：4 份报告（ASR 基准 / 两 vendor 实测 / 云端候选） | ✅ 2026-11-20 |
| Phase 2 | 架构设计：管线拆解、组件抽取方案、接口定义 | ⬅ 进行中 |
| Phase 3 | MVP 实现：transcript 管线（CLI）+ MCP Server | 待启动 |
| Phase 4 | 易用性：面向非程序员的本地界面；批量与检索 | 待启动 |

## 目录导览

- [docs/PRD.md](docs/PRD.md) — 需求文档（v1.0 已定稿）
- [docs/方案设计.md](docs/方案设计.md) — Phase 2 架构与实施方案（S1-S5 冲刺拆解）
- [docs/进度记录.md](docs/进度记录.md) — 阶段进度、决策记录、调研日志
- [docs/research/](docs/research/) — 调研与实测评估报告
- [tools/bili-subtitle/](tools/bili-subtitle/) — **自研** B站字幕抓取 CLI（扫码登录 + cookie 自动续期 + WBI 签名，零第三方依赖）
- `vendors/` — 第三方开源项目只读克隆（不入库）：[video-report-agent](https://github.com/imexlovery/video-report-agent)、[bili-transcriber](https://github.com/Asaceoo/bili-transcriber)
- `runs/` — 实测产物输出（不入库）

## 现在就能用的能力

`tools/bili-subtitle` 已可用（软链 `~/.local/bin/bili`）：

```bash
bili login                          # 扫码登录（一次）
bili info <BV号|链接>                # 标题 / UP / 时长 / 分P
bili fetch <BV号|链接>               # 抓字幕纯文本（--timestamps / --format srt|json）
```

工作规范见 [AGENTS.md](AGENTS.md)。
