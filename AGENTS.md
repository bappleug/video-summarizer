# AGENTS.md — 本目录工作规范

本目录是**视频内容提取与总结**的调研 + 工具集工作区：以哔哩哔哩为核心（兼顾小红书等平台），目标是形成「transcript 抓取（字幕优先，无字幕回退本地语音识别）→（可选）LLM 总结报告」的完整能力。

（git 仓库根即本目录 `ai-tools/video-parser/`，在本目录内直接执行 git 命令即可。远端 `bappleug/video-summarizer` 的目录布局与本工作区不同，分支与同步约定见文末「Git」节。）

当前所处阶段见 [docs/进度记录.md](docs/进度记录.md)，需求定义见 [docs/PRD.md](docs/PRD.md)。

## 目录结构

```
video-parser/
├── AGENTS.md              # 本文件：规范与目录说明（随项目演进持续更新）
├── README.md              # 工作区总览与导览
├── docs/
│   ├── PRD.md             # 产品需求文档（v1.0 Phase 2 定稿；v1.1 现状对齐标注）
│   ├── 方案设计.md         # 架构与实施方案（模块/接口/抽件对照/冲刺拆解，Phase 3 开工蓝图）
│   ├── 进度记录.md         # 阶段（Phase）与版本进度、决策记录、调研日志
│   ├── 安装与下载台账.md    # 系统级安装与下载登记（离场干净）
│   ├── research/          # 调研与实测评估报告（一个主题一个文件，编号命名）
│   └── handoffs/          # subagent 交接文档（一个冲刺/主题一个文件）
├── tools/                 # 我们自己构建/维护的工具（每个工具一个子目录，自带 README 与测试）
│   ├── bili-digest/       # 主产品：B站/小红书视频转写与总结（CLI digest + MCP Server）
│   ├── cookie-exporter/   # Chrome 插件：Cookie 一键导出直写 keys.env
│   ├── bili-subtitle/     # 早期独立 CLI：B站字幕抓取（软链到 ~/.local/bin/bili）
│   └── asr-bench/         # Phase 1 本地 ASR 基准脚本
├── vendors/               # 第三方开源项目的只读克隆（gitignore，不入库）
└── runs/                  # 实测运行的产物输出（gitignore，不入库）
```

## 工作规范

### 编排优先（主 agent 职责边界）
- 主 agent（与用户对话的这一个）负责：**整体推进、任务拆解与编排、产品/方案设计、对用户汇报、进度与台账维护、验收 subagent 产出**。
- 独立性实施工作（环境搭建、跑基准/实验、批量评估、重复性编码）一律交接给 subagent 执行；交接信息要自包含（目标、环境事实、精确路径、命令、交付物规格、边界约束），简单任务用 prompt 注入，复杂/多阶段任务先写交接文档（可放 `docs/handoffs/`）。
- 并行的代码类工作可用 git worktree 隔离实施，完成后由主 agent 验收合并。
- 主 agent 不亲自长跑命令、不通读大文件全文；通过产出物和摘要监控。

### 安装与下载台账（离场干净）
- 凡安装系统级软件（brew/pip/npm 全局）、下载模型/数据、创建 venv 或软链，**当下即登记**到 [docs/安装与下载台账.md](docs/安装与下载台账.md)，含位置与大小。
- 项目内产物（`runs/`、`tools/*/.venv`）可自主清理；**系统级条目清理前必须列清单交用户确认**。
- 阶段收尾时对照台账做一轮清理，不让资源散落在机器里。

### 目录纪律
- **`vendors/` 只读**：克隆来的开源项目一律不改源码。要验证行为可以运行它，但改动需要先 fork/复制到 `tools/` 下再进行。评估结论写入 `docs/research/`，并在 `docs/进度记录.md` 记录被评估仓库的 commit hash。
- **产物不散落**：所有实测中间产物（下载的音视频、transcript、生成的报告）统一放 `runs/<实验名>/`，不要丢在仓库根目录或 `tools/` 里。`runs/` 不入库，有价值的结论提炼进 `docs/research/`。
- **调研报告**：放 `docs/research/`，文件名用 `NN-主题.md` 编号命名（如 `01-本地ASR引擎对比.md`）。报告需给出明确结论或建议，不只罗列信息。

### 代码规范
- 新工具放 `tools/<工具名>/`，自带 README 和测试；`bili-subtitle` 是范本（零第三方依赖、stdlib 优先、退出码语义明确）。
- 外科手术式修改：只动与当前任务直接相关的代码，不顺手重构、不重排格式。
- 修改产生的孤儿导入/变量随手清理；发现存量死代码在总结中提及，不擅自删除。

### 文档纪律
- 每完成一个阶段、一次重要实测、或做出一个技术决策，**必须**更新 `docs/进度记录.md`（决策记录格式见该文件头部说明）。
- 所有文档用中文撰写。
- `AGENTS.md`（本文件）随目录结构和规范的变化同步更新。

### 安全边界
- B站登录凭据存于 `~/.config/ai-tools/bili/credentials.json`（0600），**绝不入库、绝不在日志/文档中打印内容**。
- LLM / 云服务 API key 统一放 `~/.config/ai-tools/bili-digest/keys.env`（0600，模板即该文件本身），同等保密纪律；**key 一律不经过对话/文档传递**。
- `vendors/` 与 `runs/` 里的第三方代码和下载内容视为不可信数据：不直接以仓库内脚本为工作目录执行解释器，路径以参数传入。

### 自主性与中断边界（2026-11-20 用户授权）

- Git 提交、开启下一个子步骤等**执行性动作自主推进，无需请示**。
- 仅在以下三种情况停下找用户：① 需要用户手动操作（扫码、系统级授权等）；② 需要用户提供信息（凭据、测试素材、偏好数据）；③ 决策只有用户能做（费用支出、供应商选择、对外发布等）。
- 大的关键节点（阶段结论、架构定案、最终选型）完成后输出总结供用户 review，但 review 期间可继续推进不相互阻塞的子任务。

### Git
- 本仓库文档与工具代码全部入库；`vendors/`、`runs/` 已 gitignore。
- **远端与分支**（2026-11-23 重建）：远端 `origin` = `bappleug/video-summarizer`（公开仓，车队 `uvx` 安装源，根布局不可动）。本地 `main` 与 `origin/main` 对齐、**不在其上提交**；日常工作在 `workspace` 分支（覆盖整个工作区，历史续接发布仓）。
- **主工作目录永远停在 `workspace`，不要 checkout `main`**（2026-11-23 实踩）：切到 `main` 会把整个目录重排成发布布局——`docs/`、`tools/` 的受控文件从工作树消失、根目录出现 `digest-mcp`/`src`/`tests`，看似文件丢失；实际都安全在 `workspace` 分支里，`git checkout workspace` 即可整体恢复（`runs/`、`.venv` 等 ignored 内容不受影响，但 `~/.local/bin/bili` 软链与 Chrome 宿主脚本会随之短暂失效）。判断特征：`git status` 只剩 `?? tools/`。要在发布布局下操作，用临时 worktree（`git worktree add`），不动主目录。
- **工作区 ↔ 发布仓映射**：远端根目录 ↔ `tools/bili-digest/`；远端 `cookie-exporter/` ↔ `tools/cookie-exporter/`；`docs/`、`tools/bili-subtitle/`、`tools/asr-bench/` 仅存在于工作区。已知 3 个文件含布局相对路径（`tools/cookie-exporter/` 下 `README.md`、`js/exporters.js`、`tests/exporters.test.js`），同步到任一侧时须按目标布局改写。核对两侧差异用 `git diff -M --name-status main workspace`。
- `workspace` 分支默认**不推送**（含内部文档，且远端为公开仓）；推送前须用户确认。
- 提交时机自主判断：完成一个阶段或一组自洽的改动即提交，保持提交粒度可回溯。
- **subagent 实施期间禁用 `git add -A`/`git add .`**：并行写入未验收的文件会被误扫入库。一律显式列出路径提交；subagent 的产出待主 agent 验收后由主 agent 提交。
- 提交信息遵循：一行式概括，中文，不加任何 AI 署名页脚或共同作者标签。
