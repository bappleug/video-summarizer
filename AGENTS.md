# AGENTS.md — bili-digest 给 Agent 的操作手册

> 本文件面向在本仓库或其 MCP 服务上工作的 AI Agent。人类用户请读 README.md。
> 这里汇总三类内容：① 作为消费端 Agent 该怎么用这个 MCP；② 在本机（尤其 Intel Mac / 老环境）安装、配置、排错、验证的运维要点；③ 在本仓库开发时的工作规范。
>
> 本机安装路径约定：**`~/ai-tools/video-summarizer`**（不放 Downloads）。key 与凭据走 `~/.config/ai-tools/bili-digest/`。

---

## A. 作为消费端 Agent 的使用约定

这些语义与 `src/bili_digest/mcp_server.py` 的 `INSTRUCTIONS` / `MANUAL` 一致；运行时也会通过 MCP 下发给客户端，此处是给在仓库里工作的 Agent 的完整版。

### A1. 沟通原则
- 回答使用问题时只讲场景与推荐操作，**不罗列参数表/工具清单/内部机制/费用明细**，除非用户追问。
- 用户要完整技术细节时，调 `get_manual` 取回再转述，不要凭记忆背参数。

### A2. 剧本（默认流程）
1. 想快速看懂视频 → `digest_video(url, summarize=true)` → 轮询 `job_status` 到 `done` → `get_report`。
2. 要文字稿 → `summarize=false` → `get_transcript`。
3. 要素材二次创作（做 PPT 等）→ `summarize=false` → `get_assets` / `get_frame` 自行加工。
- `type` 只在用户明说 PPT / 无语音 / 说话画面都要时传；其余不传（走 auto）。

### A3. 9 个 MCP 工具
`digest_video`（提交后台任务）→ `job_status`（轮询）→ `get_transcript` / `get_report` / `get_assets` / `get_frame`（帧图 base64）/ `cancel` / `digest_doctor`（体检）/ `get_manual`（手册）。

### A4. 关键参数语义
- `type`：`talk` 纯音频｜`slides` 画面为主（ppt 取帧 + 跳 ASR）｜`mixed` 双通道禁门控｜`auto` 默认（此时 `visual` 生效）。与 `visual` 互斥。
- `summarize`：true 由工具出 LLM 报告（费用走用户 key）；false 素材进宿主上下文自行加工。
- `reuse_cache`：默认 true，同一视频二次运行复用工作目录缓存、跳过下载；false 强制重下刷新缓存。
- `output_dir`：产物目录，绝对或相对 runs 根。

### A5. 凭据优先级（B站）
扫码 `credentials.json` > 插件导出的 `BILI_COOKIE`（keys.env；仅 credentials.json 不存在时生效）。B站已改续期机制，两种凭据都不会自动续期。`digest_doctor` 实测登录态并显示当前用哪种，失效时按其「下一步动作」处理：扫码凭据重新 `digest login`（已配 `BILI_COOKIE` 时删除 credentials.json 即可改用），插件 Cookie 回插件重新导出。key 一律填 `~/.config/ai-tools/bili-digest/keys.env`，**不经对话传递**。

### A6. 缓存语义
视频轨缓存在 **Agent 当前工作目录** `.bili-digest-cache/videos/`（MCP server 的 cwd 即 WorkBuddy 工作目录）。缓存键 = 平台+视频ID+分P cid+流ID；命中则硬链接复用、跳过下载。用户做完工作项删整个 `.bili-digest-cache/` 即可清理。`BILI_DIGEST_CACHE_DIR` 可改位置。

### A7. 进度与异步语义
- 整个 pipeline 跑完前，`job_status.progress` 一直显示「获取 transcript」，VLM 读图阶段不更新——**正常，勿误判卡死**。
- 40 帧读图约 4–5 分钟，轮询需耐心。
- `job_status` 到 `done` 后再取 report / transcript / assets；`error` 字段带修法指引。

### A8. 出错接管
任务返回 `report_error` / `visual_error` 时，直接切剧本 ②/③ 自行完成总结/读图，向用户说明该步不可用及原因；**不要替用户充值，也不要反复重试**。

---

## B. 安装、配置与运维（Intel Mac / 老环境实测）

> 来源：2026-11-22 在一台 Intel Mac（macOS 12.7、Homebrew 为 2020 老版）上的排错记录。
> Apple Silicon + 新 Homebrew 设备大多不会遇到 B2/B3/B5/B6，走 README 标准装法即可。

### B0. 一句话结论
Intel Mac + 老环境下，别用 `uvx --from ...@git+` 和 `uv pip install -e .`：前者每次启动重新拉包编译导致 **MCP initialize 超时**，后者在 Intel Mac 临时目录冲突反复失败。正确姿势是 **本地 venv + wrapper 指向源码**，再把版本/环境坑逐个锁掉。

### B1. MCP 依赖锁 2.x（源码 bug）
- 代码用 mcp 2.x 的 `Server(on_list_tools=...)`，mcp 1.x（原 pyproject 写 `mcp>=1.2`）无此参数，会在 `build_server` 报错。通用约束已修回 `mcp>=2.0`。
- 实测可用组合（本台验证通过）：

  ```
  mcp==2.3.0
  mcp-types==2.3.0
  pydantic==2.14.4
  pydantic-settings==2.7.1
  cryptography==43.0.3     # 见 B2
  httpx==0.28.1
  ```

- 首次安装显式按上述版本装，避免解析到不兼容新版。清华源 + 独立缓存加速避冲突：

  ```bash
  export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
  export UV_CACHE_DIR=/tmp/uvcache-bili-digest
  ```

### B2. cryptography 锁 43.0.3（Intel 无 wheel）
`cryptography==50` 在 Intel Mac 无预编译 wheel，退回源码编译会卡死。先装 `cryptography==43.0.3` 的 wheel 占坑，再装其余依赖。

### B3. 弃 editable install，用本地 venv + wrapper
- `uv pip install -e '.[mcp]'` 在 Intel Mac 因 uv+setuptools 临时目录 EEXIST 冲突反复失败。
- 改为：`uv venv .venv`，再按 B1 版本逐包装。
- wrapper `digest-mcp`（仓库根）指向本地源码：

  ```sh
  #!/bin/sh
  # 按脚本自身位置定位源码（解析软链），不依赖 clone 到哪个目录
  SELF=$0
  while [ -L "$SELF" ]; do
    DIR=$(cd "$(dirname "$SELF")" && pwd)
    SELF=$(readlink "$SELF")
    case $SELF in
      /*) ;;
      *) SELF="$DIR/$SELF" ;;
    esac
  done
  PROJECT_DIR=$(cd "$(dirname "$SELF")" && pwd)
  export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
  export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin${PATH:+:$PATH}"   # B4
  unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy      # B5
  exec "$PROJECT_DIR/.venv/bin/python" -m bili_digest.cli mcp "$@"
  ```

- `~/.workbuddy/mcp.json` 的 command 指向 wrapper **绝对路径** `~/ai-tools/video-summarizer/digest-mcp`。
- **教训**：WorkBuddy 自定义 MCP 一律用本地绝对命令，绝不用 `uvx+git`。
- 改完 schema 后 WorkBuddy App 需**重载 MCP** 才刷新工具定义。

### B4. wrapper 补 PATH
MCP 客户端拉起的子进程 PATH 常被裁剪，找不到 ffmpeg/uv。wrapper 中显式补（见 B3 脚本）。用 `env -i` 裁剪 PATH 模拟可验证握手正常。

### B5. wrapper unset 代理（视频下载断流根因）
- 现象：slides 模式下载 74MB，读到 9.4MB 就 `IncompleteRead`；playurl 的 baseUrl 是怪域名 `mountaintoys.cn:4483`。
- 根因：环境变量 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:59838`（AI 沙箱 `sandbox-c` 进程）把 B站 CDN 劫持到第三方域名限流断连，**与 macOS 系统代理开关无关**。
- 修复：wrapper 中清掉全部代理变量（见 B3 脚本）；B站/小红书/硅基流动均国内直连，无需代理。
- 验证：`env -u HTTP_PROXY -u HTTPS_PROXY ...` 后 baseUrl 恢复官方 `upos-sz-*.bilivideo.com`，完整下载。

### B6. ffmpeg：绕开老 brew，装 evermeet 静态包
- 本机 `/usr/local/bin/brew` 是 2020 老版，macOS 12.7 下读版本号返回 nil 直接崩（`Version value must be a string; got a NilClass`），`brew install ffmpeg` 走不通（brew 本身按用户决定不修）。
- 替代：从 evermeet.cx（macOS Intel 静态 ffmpeg 标准源）下载 `ffmpeg-9.1.zip`（tessus 构建，gpl 全编解码），解压后放 `~/.local/bin/ffmpeg`（本就在 PATH）。下载后核对 SHA256 两次一致。`digest doctor` 可确认。

### B7. VLM 读图：硅基流动要求 data URI 前缀
- 现象：40 帧抽出但「画面增强失败 HTTP 400 / code 20015 Failed to process the base64 image」，素材包 0 帧，报告降级纯文字。
- 根因：`enrich/vlm.py` 原用裸 base64（智谱 GLM 写法）；硅基流动 OpenAI 兼容端点要求 `data:image/jpeg;base64,...` 前缀。
- 已修：按 provider 区分——zhipu 裸 base64，其余用 `data:{mime};base64,{b64}`。此项已在源码，无需额外操作。
- 另注：HTTP 402 / code 30001「account balance is insufficient」是硅基流动余额不足，需到 cloud.siliconflow.cn 充值；`/v1/user/info` 已 410 废弃查不了余额。

### B8. cookie-exporter 本机助手
- 导出报「本机助手未安装」时，除检查 manifest 外，注意 `host.py` 原放 `~/Downloads/...`（700 权限）可能被 Chrome 沙箱/TCC 挡住。
- 已把 `host.py` 移到 `~/.local/bin/bili-digest-host/host.py`，更新清单 `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.raymin.cookie_exporter.json` 的 `path`。
- 改清单后需 **Cmd+Q 完全退出 Chrome 再重开**（助手清单只在启动时读）。
- 顺手修了 `install-host.sh` 里 `$HOST_PY（` 全角括号被当变量名的 bug（→ `${HOST_PY}`）。

### B9. 视频下载机制
- B站视频轨从 urllib 一次性 `read()` 改为 ffmpeg 流式（带 UA/Referer、逐个 CDN 尝试、带重试）；`.part` 后缀须带 `-f mp4`，否则 `Invalid argument`。已在源码。

### B10. 验证清单（装完照此自检）
- [ ] `digest doctor` 各能力无红项（ffmpeg / key / 登录态）
- [ ] MCP 握手：initialize + tools/list 返回 **9** 工具（protocolVersion `2025-06-18`）
- [ ] 一次性管道喂多条消息时 tools/list 可能无响应（管道时序假象）；用 Python subprocess 逐条 write+flush 才准
- [ ] PPT/slides 端到端：字幕命中 → 视频下载或缓存命中 → 抽帧 → VLM 读图 → report.md 含关键帧配图
- [ ] 测试：`PYTHONPATH=src .venv/bin/python -m pytest`（无 editable install，必须带 PYTHONPATH）

---

## C. 本机关键路径表

| 项 | 路径 |
|---|---|
| 项目源码 | `~/ai-tools/video-summarizer` |
| venv | `~/ai-tools/video-summarizer/.venv` |
| wrapper | `~/ai-tools/video-summarizer/digest-mcp` |
| MCP 配置 | `~/.workbuddy/mcp.json` |
| key 文件 | `~/.config/ai-tools/bili-digest/keys.env`（0600） |
| B站扫码凭据 | `~/.config/ai-tools/bili/credentials.json`（0600） |
| ffmpeg | `~/.local/bin/ffmpeg` |
| Chrome 助手清单 | `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.raymin.cookie_exporter.json` |
| Chrome 助手脚本 | `~/.local/bin/bili-digest-host/host.py` |
| 视频缓存（按工作项清理） | `<Agent 工作目录>/.bili-digest-cache/videos/` |

---

## D. 开发规范

以下是在本仓库开发时的工作规范（目录、编排、代码与文档纪律、安全边界、Git）；A–C 面向消费端 Agent 与部署运维。

### 目录结构

```
video-parser/                # 仓库根 = 产品根（目录名随意；远端 bappleug/video-summarizer）
├── AGENTS.md                # 本文件：Agent 操作手册（A–C）+ 开发规范（D），随项目演进持续更新
├── README.md                # 用户上手指南（安装 / MCP 接入 / CLI 速查）+ 项目结构
├── LICENSE                  # MIT
├── pyproject.toml           # 产品 bili-digest 的打包定义（CLI digest、extras、pytest 配置）
├── digest-mcp               # MCP 启动器：按自身位置找 src/ 与 .venv/
├── src/bili_digest/         # 主产品源码：B站/小红书视频转写与总结（CLI digest + MCP Server）
├── tests/                   # 产品单测（pytest，离线）
├── cookie-exporter/         # Chrome 插件：Cookie 一键导出直写 keys.env
├── docs/
│   ├── PRD.md               # 产品需求文档（v1.0 Phase 2 定稿；v1.1 现状对齐标注）
│   ├── 方案设计.md          # 架构与实施方案（模块/接口/抽件对照/冲刺拆解，Phase 3 开工蓝图）
│   ├── 进度记录.md          # 阶段（Phase）与版本进度、决策记录、调研日志
│   ├── 安装与下载台账.md    # 系统级安装与下载登记（离场干净）
│   ├── research/            # 调研与实测评估报告（一个主题一个文件，编号命名）
│   └── handoffs/            # subagent 交接文档（一个冲刺/主题一个文件）
├── scripts/                 # 独立脚本（每个一个子目录，自带 README 与测试）
│   ├── bili-subtitle/       # 早期独立 CLI：B站字幕抓取（软链到 ~/.local/bin/bili）
│   └── asr-bench/           # Phase 1 本地 ASR 基准脚本
├── vendors/                 # 第三方开源项目的只读克隆（gitignore，不入库）
├── runs/                    # 实测运行的产物输出（gitignore，不入库）
└── .venv/                   # 开发与 MCP 运行的 venv（gitignore，不入库）
```

### 编排优先（主 agent 职责边界）
- 主 agent（与用户对话的这一个）负责：**整体推进、任务拆解与编排、产品/方案设计、对用户汇报、进度与台账维护、验收 subagent 产出**。
- 独立性实施工作（环境搭建、跑基准/实验、批量评估、重复性编码）一律交接给 subagent 执行；交接信息要自包含（目标、环境事实、精确路径、命令、交付物规格、边界约束），简单任务用 prompt 注入，复杂/多阶段任务先写交接文档（可放 `docs/handoffs/`）。
- 并行的代码类工作可用 git worktree 隔离实施，完成后由主 agent 验收合并。
- 主 agent 不亲自长跑命令、不通读大文件全文；通过产出物和摘要监控。

### 安装与下载台账（离场干净）
- 凡安装系统级软件（brew/pip/npm 全局）、下载模型/数据、创建 venv 或软链，**当下即登记**到 [docs/安装与下载台账.md](docs/安装与下载台账.md)，含位置与大小。
- 项目内产物（`runs/`、`.venv/`、`scripts/*/.venv`）可自主清理；**系统级条目清理前必须列清单交用户确认**。
- 阶段收尾时对照台账做一轮清理，不让资源散落在机器里。

### 目录纪律
- **`vendors/` 只读**：克隆来的开源项目一律不改源码。要验证行为可以运行它，但改动需要先 fork/复制到 `scripts/` 下再进行。评估结论写入 `docs/research/`，并在 `docs/进度记录.md` 记录被评估仓库的 commit hash。
- **产物不散落**：所有实测中间产物（下载的音视频、transcript、生成的报告）统一放 `runs/<实验名>/`，不要丢在仓库根目录或 `scripts/` 里。`runs/` 不入库，有价值的结论提炼进 `docs/research/`。
- **调研报告**：放 `docs/research/`，文件名用 `NN-主题.md` 编号命名（如 `01-本地ASR引擎对比.md`）。报告需给出明确结论或建议，不只罗列信息。

### 代码规范
- 产品代码进 `src/bili_digest/`；独立脚本放 `scripts/<脚本名>/`，自带 README 和测试；`scripts/bili-subtitle` 是范本（零第三方依赖、stdlib 优先、退出码语义明确）。
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
- **远端与分支**：远端 `origin` = `bappleug/video-summarizer`（公开仓，车队 `uvx` 安装源）。`main` 是唯一长期分支，日常工作直接在 `main` 上提交（2026-11-23 布局二合一后不再有布局不同的第二个分支，原 `workspace` 分支并入 `main` 后删除，见 D-016）。
- **根布局不可随意变动**：`pyproject.toml`、`digest-mcp` 必须在仓库根——车队各机的 MCP 配置指向 clone 目录下的 `digest-mcp`，`uvx` 从根目录 `pyproject.toml` 安装。布局变更的先例见 [布局二合一迁移指引](docs/迁移指引-2026-11-布局二合一.md)。
- 提交时机自主判断：完成一个阶段或一组自洽的改动即提交，保持提交粒度可回溯。
- **推送前须用户确认**（远端为公开仓，且是车队安装源）。
- **subagent 实施期间禁用 `git add -A`/`git add .`**：并行写入未验收的文件会被误扫入库。一律显式列出路径提交；subagent 的产出待主 agent 验收后由主 agent 提交。
- 提交信息遵循：一行式概括，中文，不加任何 AI 署名页脚或共同作者标签。
