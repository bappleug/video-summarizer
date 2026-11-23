# bili-digest

B站 / 小红书视频转写与总结工具：一条命令把视频变成文字——**字幕优先（带污染校验）→ 无字幕自动回退语音识别（本地 mlx / 云端免费 SenseVoice）→（可选）LLM 中文总结报告 →（可选）关键帧画面增强**。支持 CLI 与 MCP 两种用法。

- 零配置可用：核心零第三方依赖，B站字幕路径开箱即用
- 一把 key 全搞定：默认走硅基流动（免费注册），读图 / 总结 / 云转写共用一个 key
- 面向 Agent：内置 9 个 MCP 工具，AI 客户端接入后对它说「开始用 bili-digest」即可被引导完成配置

## 安装 cookie 导出插件（可选，用小红书时需要）

小红书取材需要登录态 Cookie；B站也可用它导出 Cookie 作为扫码登录之外的回退。
仓库内置了配套的 Chrome 插件 **cookie-exporter**，
装好后可一键把当前浏览器的 Cookie 直写到 `~/.config/ai-tools/bili-digest/keys.env`：

1. **获取仓库**：`git clone https://github.com/bappleug/video-summarizer`（或 GitHub 页面
   下载 zip 解压）；
2. **加载扩展**：Chrome 打开 `chrome://extensions` → 右上角开「开发者模式」→
   「加载已解压的扩展程序」→ 选中仓库内的 `cookie-exporter/` 目录；
3. **一键导出**：在登录了小红书的 Chrome 里点插件图标 → 「导出到 keys.env」。

**本机助手（直写依赖，一次性）**：首次使用前跑
`bash cookie-exporter/native/install-host.sh` 并**完全退出 Chrome（Cmd+Q）后重开**；
卸载用 `uninstall-host.sh`。

**安全性**：插件不发任何网络请求——manifest 仅声明 `cookies` 读取 +
`nativeMessaging`（连本机助手）+ `storage`（记住偏好），`host_permissions` 只覆盖
小红书 / B站两个域；只导出白名单字段（小红书 `a1/web_session` 等），host 助手
固定写 0600 的 keys.env，不上传、不打印。

## 三步上手

### ① 把项目下载到 `~/ai-tools`

建议统一放在 `~/ai-tools` 下（与个人 AI 工具、key 配置同目录，便于集中管理，别丢 Downloads）：

```bash
mkdir -p ~/ai-tools
git clone https://github.com/bappleug/video-summarizer ~/ai-tools/video-summarizer
```

目标是本地路径 `~/ai-tools/video-summarizer/`——后面的 MCP 配置和 CLI 都基于它。已装过可跳过这步。

放在 `~/ai-tools` 只是建议位置，clone 到任意目录均可：`digest-mcp` 按自身位置定位源码，把下文示例路径换成你的 clone 目录即可。

### ② 接入你的 MCP 客户端

MCP 直接指向**本地源码目录自带的启动脚本** `digest-mcp`（仓库根目录，已带可执行权限）。先确认它可执行、依赖就绪：

```bash
cd ~/ai-tools/video-summarizer
chmod +x digest-mcp
# 首次装依赖：uv 建环境 + 装 MCP 运行所需包（详见 AGENTS.md「依赖版本锁定」）
uv venv .venv
uv pip install 'mcp>=2.0' mcp-types pydantic pydantic-settings cryptography
```

**通用**（Cursor / Claude Desktop / Claude Code / ZCode 等支持 `mcpServers` JSON 的客户端）——`command` 填 wrapper 的**绝对路径**（`~` 不展开，用完整路径）：

```json
{
  "mcpServers": {
    "bili-digest": {
      "command": "/Users/<你的用户名>/ai-tools/video-summarizer/digest-mcp",
      "args": []
    }
  }
}
```

**Work Buddy / 豆包等在设置界面填表单的客户端**：找到「MCP 服务器 / 工具 / 扩展」入口，新建 stdio 类型服务器，逐项填入——

- 名称：`bili-digest`
- 命令：`/Users/<你的用户名>/ai-tools/video-summarizer/digest-mcp`（绝对路径）
- 参数：留空

两个注意点：

- 路径里 `<你的用户名>` 换成实际用户名（终端 `echo $HOME` 查）；key 走 `keys.env` 文件（见下「凭据与安全」），不要塞进 JSON。
- 改完配置要在客户端里**重载 MCP** 才会生效。

> Intel Mac / 老环境的依赖版本锁定、代理劫持、ffmpeg、cookie 助手等坑，见 [AGENTS.md](AGENTS.md) 的「安装、配置与运维」一节。若不方便 clone、只想临时用一下，可用 uvx 免安装：`command` 填 `uvx`，参数填 `--from bili-digest[mcp]@git+https://github.com/bappleug/video-summarizer digest mcp`（首次拉包慢，可能触发 `initialize timed out`，故不作为首选）。

### ③ 对 Agent 说：「开始用 bili-digest」

Agent 会调用 `digest_doctor` 体检工具，按「能力 × 状态 × 下一步动作」引导你完成 key 申请、`keys.env` 写入、B站扫码登录等配置，之后直接丢视频链接即可。

**提示词样例**（对着你的 Agent 照说即可，都是日常口语，不用记参数）：

| 场景 | 你可以这样说 | Agent 会做什么 |
|---|---|---|
| 首次使用 | 「开始用 bili-digest」 | 先体检，缺什么引导你配什么（key 自己填文件，不会让你贴进对话） |
| 看懂一个视频 | 「帮我总结这个视频：<链接>」 | 提交任务 → 出带时间戳要点的中文报告 |
| 知道是讲义/PPT | 「总结这个视频，它就是个 PPT 课件：<链接>」 | 按幻灯片模式取每一页最全的画面来读 |
| 知道没有语音 | 「这个视频没有语音，画面就是全部内容：<链接>」 | 跳过语音转写，直接识别画面文字出报告 |
| 只要文字稿 | 「把这个视频的完整文字稿给我：<链接>」 | 返回全文（可要 txt/srt/带时间戳格式），不花总结的钱 |
| 拿素材做二次创作 | 「我要用这个视频的截图和文字稿自己做 PPT：<链接>」 | 给你关键帧清单+图片+全文，由 Agent 陪你加工 |
| 某一步失败时 | 「报告没生成，怎么回事？」 | 按工具返回的原因给修法（如余额不足附充值入口），或直接拿文字稿先给你总结 |
| 查完整用法 | 「显示完整技术手册」 | 解锁参数表、工具清单等开发者级说明（平时不会拿这些烦你） |

不用 MCP 也可以纯 CLI（在本地仓库目录下跑）：

```bash
cd ~/ai-tools/video-summarizer
.venv/bin/python -m bili_digest.cli BV1CV411s7jd
```

## CLI 速查

后文用 `digest` 代指命令本体（在本地仓库目录下即 `.venv/bin/python -m bili_digest.cli`）。

| 命令 | 作用 |
|---|---|
| `digest BV1CV411s7jd` | 抓 transcript，txt 输出到 stdout |
| `digest 'https://www.bilibili.com/video/BV...?p=5'` | URL 自动识别分P；无字幕走 ASR |
| `digest <目标> --page 5 --format srt` | 指定分P；格式 `srt / md / json`（默认 txt） |
| `digest <目标> --engine cloud` | 云端 SenseVoice 转写（免费，需 key）；默认 auto |
| `digest <目标> --summarize` | transcript + LLM 中文报告（落盘 `runs/<id>/<时间戳>/`） |
| `digest <目标> --summarize --provider deepseek` | 换 DeepSeek 出报告（默认硅基流动） |
| `digest 'https://xhslink.cn/xxxx'` | 小红书视频笔记（支持 App 分享短链） |
| `digest <目标> --type slides --no-report` | 素材场景：画面为主，只要素材包不出报告 |
| `digest <目标> --visual --summarize` | 画面增强 + 嵌帧报告（`--visual=ppt` 为讲义模式） |
| `digest <目标> --no-reuse-cache` | 强制重下并刷新视频缓存（默认复用缓存） |
| `digest report runs/BV.../<run-id>` | 对已有 transcript 补生成报告 |
| `digest doctor` | 环境与 key 体检（已配置的 key 会真实 ping，约 ¥0.001/项；B站登录态实测 nav 接口） |
| `digest login` / `digest status` | B站扫码登录 / 查看登录态 |
| `digest mcp` | 启动 MCP stdio server（需 `[mcp]` extra） |

内容走 stdout，进度与告警走 stderr。退出码：0 成功 / 2 用法错误 / 3 未登录且转写失败 / 4 网络或风控 / 1 其它。

## 能力概览

- **平台**：B站（BV号 / av号 / URL，含分P）与小红书（App 分享链接 / 短链，视频笔记）。
- **字幕优先 + 四规则防污染**：优先抓现成字幕轨；下载后先过四条校验——时长覆盖 ≥60%、中文占比 ≥50%、连续重复行 <30%、标题/简介 bigram 命中 ≥15%——任一不过自动降级 ASR（stderr 注明触发规则，不报错）。
- **双 ASR 引擎**：Apple Silicon 本地 mlx-whisper（`[mlx]` extra，离线）；云端硅基流动 SenseVoice（免费，`--engine cloud`）。默认 auto：本地可用走本地。
- **`--type` 内容形态**（推荐）：`talk`（内容在说话，纯音频）/ `slides`（内容在画面，PPT 取帧 + 跳过 ASR）/ `mixed`（双通道都要）/ `auto`（默认）。与 `--visual` 互斥。
- **`--visual` 画面增强**：敏感边界检测取帧 + pHash 聚簇取每页最全状态 + VLM 逐帧读图（转录画面文字），与 transcript 按时间戳融合成富化时间轴；auto 模式带画面价值门控，口播类视频自动诚实降级不浪费读图费。读图约 ¥0.001-0.05/条视频。
- **素材包**：`assets.json`（机读：帧清单 + 描述 + 时间点 + transcript 概要）与 `assets.md`（人读），MCP `get_assets` / `get_frame` 可直接回放。
- **视频缓存**：`--visual`/`slides`/`mixed` 下载的视频轨缓存在**当前工作目录**的 `.bili-digest-cache/videos/`（MCP server 的 cwd 即 Agent 工作目录）。缓存键为 `平台+视频ID+分P+流ID`，同一视频二次运行直接复用、跳过下载；清晰度/编码变化自动换键。用户做完工作项后，删整个 `.bili-digest-cache/` 即可手动清理视频缓存。`reuse_cache=false`（MCP）或 `--no-reuse-cache`（CLI）可强制重下刷新。环境变量 `BILI_DIGEST_CACHE_DIR` 可改缓存位置。
- **LLM 报告**：`--summarize` 一次调用出结构化 Markdown 报告（约 ¥0.005–0.02/次；默认硅基流动约 ¥0.005），usage 与成本记入 run 目录 `meta.json`；网络错误自动退避重试。
- **MCP server**（`digest mcp`，9 工具）：`digest_video`（提交后台任务）/ `job_status` / `get_transcript` / `get_report` / `get_assets` / `get_frame`（帧图 base64，多模态客户端直接看图）/ `cancel` / `digest_doctor` / `get_manual`（完整技术手册，按需取用）。

## 凭据与安全

- 所有云服务 key 统一放 `~/.config/ai-tools/bili-digest/keys.env`（权限 0600）：`SILICONFLOW_API_KEY`（默认全能力：读图 + 总结 + 云转写）、`ZHIPU_API_KEY` / `DEEPSEEK_API_KEY`（可选备用）、`XHS_COOKIE`（小红书，浏览器登录后复制整条 Cookie 值）。也支持同名环境变量。
- B站登录凭据优先取 `~/.config/ai-tools/bili/credentials.json`（0600），`digest login` 扫码获取；仅当该文件不存在时，才回退读取 `BILI_COOKIE`（即 Chrome 插件 cookie-exporter 导出的 Cookie 字符串，写在 keys.env，无需扫码）。**B站已改续期机制，两种凭据都不会自动续期**，失效后需重新登录：扫码凭据重新 `digest login`（若已配置 `BILI_COOKIE`，删除 credentials.json 即可改用插件 Cookie）；插件 Cookie 回插件重新导出。`digest status` / `digest doctor` 会实测登录态，并告诉你当前用的是哪种、失效后怎么办。
- **key 永远不通过对话、issue 或文档传递**；本工具也不会把 key 打进任何日志。
- 可选覆盖变量（keys.env 或环境变量）：`VISION_PROVIDER` / `SUMMARIZE_PROVIDER`（`siliconflow|zhipu|deepseek`）、`VISION_MODEL`（读图模型覆盖）。

## 已知限制

- **B站登录态**：多数公开视频不登录也能抓字幕；登录后更稳。B站已改续期机制，自动续期不可用（扫码凭据虽带 refresh_token 也无法续期）；失效后 `digest status` / `digest doctor` 会明确提示，按提示重新 `digest login`（扫码凭据）或回 Chrome 插件重新导出 `BILI_COOKIE`（插件 Cookie）。
- **小红书反爬**：风控比 B站严——匿名访问分享页常被安全拦截（error_code 300031），实测 `XHS_COOKIE` 事实必需；分享链接的 `xsec_token` 有时效，过期请从 App 重新分享取新链接。仅支持视频笔记，图文不支持（退出码 2 明确提示）。
- **云转写时长**：硅基流动 SenseVoice 单文件 ≤1 小时；超长视频请用本地 mlx 引擎。
- **本地引擎**：mlx 仅 Apple Silicon 可用；`--visual` 需要系统 ffmpeg（`brew install ffmpeg`）。

## 开发

```bash
git clone https://github.com/bappleug/video-summarizer ~/ai-tools/video-summarizer
cd ~/ai-tools/video-summarizer
uv venv
uv pip install -e ".[dev]"        # Apple Silicon 本地转写：".[dev,mlx]"
.venv/bin/python -m pytest        # 全量单测（离线）
```

> 若仓库是「装依赖而非 editable install」方式装的（见 AGENTS.md），跑测试需带 `PYTHONPATH=src`。

需要 Python ≥ 3.11 与系统 ffmpeg。核心零第三方依赖（字幕路径不装任何 extra 即可用）。

## 致谢与许可

MIT License（见 [LICENSE](LICENSE)）。小红书页面解析模块抽自开源项目 [video-report-agent](https://github.com/imexlovery/video-report-agent)（MIT），其余为本项目实现。
