# video-parser — 视频内容提取与总结工作区

围绕**哔哩哔哩**与**小红书**视频，形成一套「视频 → 文字 → 报告」的工作区。主产品是 [tools/bili-digest/](tools/bili-digest/)（Python 包 `bili_digest`，CLI `digest` + MCP stdio Server，已发布 [bappleug/video-summarizer](https://github.com/bappleug/video-summarizer) v1.2.0，MIT）：

```
视频 URL → 字幕抓取（优先，四规则防污染） → 无字幕时 ASR（云端硅基流动保底，Apple Silicon 可选本地 mlx）→ transcript →（可选）LLM 总结报告与画面关键帧
```

面向两类用户：AI Agent 宿主（经 MCP 接入，Work Buddy 已联调通过；豆包客户端未联调）与非程序员（现阶段经 Agent 宿主使用，独立图形界面属 Phase 4、未排期）。

工作区原名 `bili`，2026-11-23 更名为 `video-parser`；`ai-tools/` 只是本地外层容器目录（不建仓），git 仓库根即本工作区。

## 当前状态

**Phase 3（MVP：transcript 管线 CLI + MCP Server）已完成并发布 v1.2.0**。冲刺 S1–S4、S6（含 S6.1–S6.5）、S7（含 S7.2–S7.6）、S8 完成；**S5（SQLite 历史 + 批量队列）不做（D-015）**；Phase 4 未启动（其中小红书支持已在 S4 完成）。详见 [进度记录](docs/进度记录.md)。

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | 工作区搭建：目录重构、文档骨架、vendor 克隆 | ✅ 2026-11-20 |
| Phase 1 | 调研实测：ASR 基准、两 vendor 实测、云端候选（research/01–05） | ✅ 2026-11-20 |
| Phase 2 | 架构设计：PRD 定稿、管线拆解、组件抽取方案 | ✅ 2026-11-20 |
| Phase 3 | MVP：transcript 管线（CLI）+ MCP Server | ✅ 已发布 v1.2.0（S5 不做，D-015） |
| Phase 4 | 易用性：面向非程序员的本地界面；批量与检索 | 待启动（小红书支持已在 S4 完成） |

## 目录导览

- [docs/PRD.md](docs/PRD.md) — 产品需求文档（v1.1，已按现状对齐标注）
- [docs/方案设计.md](docs/方案设计.md) — 架构与实施方案（模块 / 接口 / 抽件对照 / 冲刺拆解）
- [docs/进度记录.md](docs/进度记录.md) — 阶段进度、决策记录、调研日志
- [docs/安装与下载台账.md](docs/安装与下载台账.md) — 系统级安装与下载登记（离场干净）
- [docs/research/](docs/research/) — 调研与实测评估报告（01–05）
- [docs/handoffs/](docs/handoffs/) — subagent 交接文档（按冲刺/主题拆分）
- [tools/bili-digest/](tools/bili-digest/) — **主产品**：B站 / 小红书视频转写与总结（用法见其 [README](tools/bili-digest/README.md)）
- [tools/cookie-exporter/](tools/cookie-exporter/) — Chrome 插件：把小红书 / B站 Cookie 一键直写 `keys.env`
- [tools/bili-subtitle/](tools/bili-subtitle/) — 早期独立 CLI：B站字幕抓取（软链 `~/.local/bin/bili`，逻辑已并入 bili-digest）
- [tools/asr-bench/](tools/asr-bench/) — Phase 1 本地 ASR 基准脚本
- `vendors/` — 第三方开源项目只读克隆（gitignore，不入库；评估已完成）
- `runs/` — 实测产物输出（不入库）

## 现在就能用

安装、MCP 接入与完整参数见 [tools/bili-digest/README.md](tools/bili-digest/README.md)。最常用的 `digest` 命令（工作区内的可执行文件是 `tools/bili-digest/.venv/bin/digest`，下文简写为 `digest`）：

```bash
digest BV1CV411s7jd                            # 抓 transcript（txt 到 stdout）
digest 'https://www.bilibili.com/video/BV...?p=5' --format srt   # URL 自动识别分P，输出 srt
digest <目标> --summarize                       # + LLM 中文总结报告（默认硅基流动）
digest 'https://xhslink.cn/xxxx' --type slides --no-report      # 小红书 PPT 类视频：只要素材包不出报告
digest doctor                                  # 环境与 key 体检（能力 × 状态 × 下一步动作）
```

旧工具 `bili`（`tools/bili-subtitle/`）仍可一条命令抓 B站字幕；新需求一律用 `digest`。

## 与发布仓的关系

发布仓 [bappleug/video-summarizer](https://github.com/bappleug/video-summarizer) 的根目录对应本工作区 `tools/bili-digest/`，其 `cookie-exporter/` 对应 `tools/cookie-exporter/`；`docs/` 等仅存在于本工作区。分支与双向同步约定见 [AGENTS.md](AGENTS.md) 的「Git」节。

工作规范（编排、目录纪律、文档纪律、安全边界）见 [AGENTS.md](AGENTS.md)。
