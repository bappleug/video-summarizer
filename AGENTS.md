# AGENTS.md — bili-digest 给 Agent 的操作手册

> 本文件面向在本仓库或其 MCP 服务上工作的 AI Agent。人类用户请读 README.md。
> 这里汇总两类内容：① 作为消费端 Agent 该怎么用这个 MCP；② 在本机（尤其 Intel Mac / 老环境）安装、配置、排错、验证的运维要点。
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
扫码 `credentials.json`（带 refresh_token 自动续期） > 插件导出的 `BILI_COOKIE`（keys.env，无续期，过期需重新导出）。`digest_doctor` 显示当前用哪种。key 一律填 `~/.config/ai-tools/bili-digest/keys.env`，**不经对话传递**。

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
  PROJECT_DIR="$HOME/ai-tools/video-summarizer"
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
