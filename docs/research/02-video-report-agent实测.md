# video-report-agent 实测评估（B站/小红书视频总结工具选型）

> Phase 1 调研 · 2026-11-20 · 结论见文末「选型结论」

- 被评估项目：`vendors/video-report-agent`（af73abd，2026-11-18，372★），Python 3.12 + uv + FFmpeg + Node≥22.19，支持 B站/YouTube/抖音/小红书。字幕优先、无字幕回退 ASR（默认百炼云端，Apple Silicon 可选本地 MLX Whisper），LLM 生成 HTML 报告。
- 评估环境：Apple M4 Pro / 64GB / macOS，node 24.14.0、ffmpeg 9.0.1 已装，uv 未装（本次装上）。**无任何 LLM / 云端 ASR 的 API key**，涉及 key 的功能只走读代码。
- 全部运行与产物在 `runs/eval-vra/`（源码副本 `runs/eval-vra/src`，venv `runs/eval-vra/venv`），vendors/ 未动。

## 0. TL;DR

- **安装零摩擦**：`brew install uv` + `cp -R` + `uv sync --extra mlx` 三步完成，无报错；venv 1.2GB（torch 491MB 是大头）。
- **无 key 转写实测成功**：`transcribe --asr-backend mlx` 对 BV1CV411s7jd?p=5（311 秒中文、无字幕）端到端 34.5 秒，MLX Whisper 纯转写 12.5 秒（≈25× 实时，与 01 报告一致），175 段、覆盖全时长、质量可用。
- **B站匿名拿字幕 → 如实报 LOGIN_REQUIRED → 回退 ASR**，有 SESSDATA/浏览器 cookie 导入机制，不是匿名硬抓；对字幕垃圾有校验但无完整风控应对。
- **核心卖点（LLM 报告）强绑定 Pi RPC agent（npm 工具）+ LLM key**，无 key 完全不可用；本地 ASR 仅 MLX，**Intel Mac 不可用**。
- **结论：不整体采用，定向抽 3 个组件**（小红书取材模块、B站字幕校验/ai-zh 解析、报告 Skill 模板），理由见文末。

## 1. 安装过程实录

| 步骤 | 命令 | 结果 |
|---|---|---|
| 1 | `brew install uv` | 成功。uv 0.13.9（Homebrew 2026-11-17 aarch64），/opt/homebrew/Cellar/uv/0.13.9，17 files 34.3MB |
| 2 | `cp -R vendors/video-report-agent runs/eval-vra/src` | 成功，13MB |
| 3 | `UV_PROJECT_ENVIRONMENT=runs/eval-vra/venv uv sync --extra mlx`（在 src 内） | **一次成功，0 报错、0 人工干预**。装齐依赖 + dev 组 + mlx extra，venv 1.2GB，Python 3.12.16（uv 自管） |

- 按 README/docs/cli.md 的完整安装还有两步，本次按需跳过并验证不影响转写：
  - `uv run playwright install chromium`：只有 generate 导出 PNG 长图需要。未装，transcribe 正常（但项目自带测试套件里 18 个用例因缺浏览器失败，见 §4）。
  - `npm install -g @earendil-works/pi-coding-agent@0.85.0`：只有 LLM 报告生成需要。未装，transcribe 不触碰。
- `.env` 未创建：`--asr-backend mlx` 显式指定后本地转写不需要任何配置文件或 key。
- 摩擦计：实际操作 3 条命令、报错 0 次。

## 2. 转写实测（无 key 路线，本地 MLX Whisper）

```sh
cd runs/eval-vra
HF_ENDPOINT=https://hf-mirror.com ./venv/bin/video-report --runs runs transcribe \
  'https://www.bilibili.com/video/BV1CV411s7jd?p=5' --asr-backend mlx
```

**结果：成功，退出码 0，总耗时 34.5 秒**（10:51:20 → 10:51:55）。

### 2.1 流水线行为

- 选源 auto：先探字幕。匿名请求 `/x/web-interface/view` + `/x/player/wbi/v2` + `/x/v2/subtitle/web/view`，两个字幕源均如实返回 `LOGIN_REQUIRED`（与我们已知事实「匿名 0 轨」一致），**没有误抓、没有报错中断**，自动落到 ASR。
- 音频：yt-dlp 下载 DASH 音轨 m4a 12.4MB（~5.6MB/s，主 CDN 失败自动换 backupUrl 节点，download.log 有节点切换记录）→ ffmpeg 转 16kHz 单声道 WAV（audio.py 内置 ffprobe 校验声道/采样率）。
- ASR：`mlx_whisper.transcribe(path_or_hf_repo='mlx-community/whisper-large-v3-turbo')`，独立 spawn 进程跑（防 Metal 缓存泄漏），**纯转写 12,488ms**（311.4s 音频 ≈ 25× 实时，与 01 报告 mlx turbo 24.2× 吻合）。
- 模型来源说明：该模型**本次未重新下载**——worker.log 显示 HF 4 个文件 0 秒完成（缓存命中）；权重 blob（1.6GB）落盘于当日 10:32，系此前 asr-bench 评估（01 报告）预下载。首次全新下载需 `HF_ENDPOINT=https://hf-mirror.com`，一次性 ~1.6GB。

### 2.2 产物（runs/eval-vra/runs/9e296b0eef…/）

| 文件 | 内容 |
|---|---|
| `transcript.md` | 175 段、7,622 字符，格式 `[unit-000001 \| 0.000–5.600s] 文本`，带标题/UP主/attribution 头 |
| `asr.json` | backend=mlx / provider=local / engine=mlx-whisper / model / elapsed_ms / 全部原始分段（已脱敏） |
| `bilibili-subtitles.json` | 视频元数据（aid 418762389、cid 360013659、分P 5、时长 311402ms）+ 两字幕源 LOGIN_REQUIRED 状态 |
| 其余 | `audio.wav`(10MB)、`download/source.m4a`、`download.log`、`worker.log`、`status.json`、`run.trace.jsonl`、`state.sqlite3`（同目录 runs/） |

### 2.3 转写质量抽查

- 标题、UP主（哔哩哔哩弹幕网）、简介、分P 解析全部正确。
- 正文覆盖 0–312.68s 全时长；开头「大家好 我是老番茄…」等识别准确、自带标点。
- 典型 ASR 误差：UP 主名个别错字（「我是中国波雅」「老子长」），属 whisper large-v3-turbo 正常水平（01 报告交叉 CER 1.165% 同款模型）。
- 未见风控污染（字幕根本没拿到，直接走了 ASR，链路干净）。

## 3. 四个关键问题（读代码结论）

### a. B站字幕获取链路：匿名 or cookie？`src/video_report_agent/bilibili_subtitles.py`（427 行）

- **有 cookie 导入，非匿名硬抓**。`read_sessdata()`：优先环境变量 `BILIBILI_SESSDATA`（只取 SESSDATA 一个 cookie），否则 `BILIBILI_SUBTITLE_BROWSER=chrome|edge…` 用 yt-dlp 的 `extract_cookies_from_browser` 从本机浏览器现读（不落盘）。CLI `--browser/--cookies` 走另一条 `source_access` 路径（字幕+媒体统一登录回退）。我们自研 bili-subtitle 已解决登录态，此机制对其是替代而非依赖。
- **aid/cid**：`/x/web-interface/view?bvid=` → `pages[page-1].cid`（分P 正确处理），aid 取自响应并回验 `bvid` 一致。
- **字幕轨两条路**：① 旧接口 `/x/player/wbi/v2`——**自实现 WBI 签名**（匿名打 `/x/web-interface/nav` 拿 wbi_img key，内置 33 位混淆表 WBI_ORDER 算 w_rid，注释明确「匿名 nav 返回 -101 但仍给 WBI keys」）；② 新接口 `/x/v2/subtitle/web/view`（ai-zh AI 字幕）——**自写 protobuf varint/wire 解析器**（参考 ccBilly-aipm/bilibili-ai-subtitle，MIT）。
- **风控应对（部分）**：无退避/重试；但有四道校验——(1) player 接口返回的 aid/cid 必须与请求一致（identity mismatch 直接弃），能挡串号响应；(2) `validate_cues`：cue 时间严格单调、end>start、`end*1000 ≤ 时长+5000ms` 超限即弃，能挡明显超时长的垃圾字幕，但**挡不住「时长合法却内容无关」的污染字幕**；(3) 字幕 URL 域名白名单（aisubtitle.hdslb.com / i0-i2.hdslb.com / subtitle.bilibili.com）防 SSRF；(4) 覆盖率统计（timed_ratio/largest_gap）写入产物供下游判断。
- 无可用轨时的状态语义清晰：匿名→`LOGIN_REQUIRED`（提示登录而非误判无字幕），已登录→`FAILED/ABSENT`。

### b. 小红书支持怎么实现？`src/video_report_agent/xiaohongshu.py`（165 行）

- **基于 yt-dlp 的 `XiaoHongShuIE` 提取器做 monkey-patch**（替换 `_real_extract`），不是自研 HTTP 客户端，也不是官方 API。
- 链接层：自实现分享文字/短链解析（xhslink.com 最多 5 跳重定向），**保留 `xsec_token`/`xsec_source` 分享参数**（小红书访问必需）。
- 页面层：从 `window.__INITIAL_STATE__` 解析 note 详情；校验必须是视频笔记；**字幕从 `video.mediaV2.video.subtitles` 提取**（仅收 .srt URL）。
- 登录策略：**匿名优先**；401 或跳登录页 → `PLATFORM_LOGIN_REQUIRED` → 才读 `XIAOHONGSHU_COOKIE` 环境变量 / `--cookies` 文件 / `--browser`（配套 `scripts/export_platform_cookies.py` 导出工具，文件权限 0600）重试一次。链接失效/签名拒绝不会自动升级登录。
- 判断：这是全项目对我们最有价值的独立模块（我们规划里小红书还没方案），依赖仅 yt-dlp + httpx，抽用成本低。

### c. 报告生成需要哪些外部服务/key？

| 用途 | 配置 | 默认值 | 必需性 |
|---|---|---|---|
| 报告 LLM | `.env`: `PI_PROVIDER`/`PI_MODEL`/`PI_API_KEY`，经 **npm 包 @earendil-works/pi-coding-agent@0.85.0**（Pi RPC agent，负责模型调用+工具+Agent 循环） | `deepseek` + `deepseek-flash`；包内预置 zhipu（BigModel `glm-5.3-flash`，open.bigmodel.cn）、deepseek、heyroute（中转）三家 models.json，Pi 生态可换其他 provider | **generate 必需**；transcribe/preflight/fetch-* 不需要 |
| 云端 ASR | `DASHSCOPE_API_KEY`（阿里百炼） | auto 选源进 ASR 时默认 `qwen-audio-3.1-asr-flash-filetrans`（Qwen Audio 3.1 Filetrans），可换 paraformer-v2 / fun-asr 系列；`pipeline.py:129` auto 模式无显式 backend 时**强制 paraformer 云端** | 仅本地 MLX 不可用时；显式 `--asr-backend mlx` 可完全绕开 |
| 术语纠错搜索（可选） | `DEEPSEEK_SEARCH_API_KEY`（默认 provider）或 `OPENAI_SEARCH_API_KEY`+`OPENAI_SEARCH_MODEL` | DeepSeek Search / OpenAI Responses Web Search（默认 `gpt-6.1-sol`） | 仅 `--term-correction` 时，每任务限 3 次 |
| 独立绘图模型（可选） | `DIAGRAM_PROVIDER`/`DIAGRAM_MODEL` | 沿用主报告模型认证 | Standard 按需 |

- 费用入口：本地 CLI 各凭各的官方 key；托管版 VReport 网站的账户/额度属于独立闭源 Web Service，不在开源 Core 内。
- **无 key 时可用面**：transcribe（mlx）、preflight、fetch-source、fetch-douyin、fetch-xiaohongshu、audit-subtitles——即「取材+转写」全免费，「总结报告」完全没有免费路径。

### d. 音频下载用什么？依赖多重？

- **yt-dlp**（Python 包，venv 内 15MB），外包一层子进程 worker（`download_worker.py`）：monkey-patch B站提取器实现 **backupUrl 备用节点重试**（download.log 里实测切换过 5 个 upos-sz-* 节点）与**充电「试看」内容检测**（拒绝把试看当完整视频）；抖音/小红书提取器补丁也在这里注入。ffmpeg 转 16kHz mono WAV 并 ffprobe 校验。
- 与我们 `scripts/asr-bench/fetch_audio.py`（`x/player/playurl?fnval=16` 自请求）思路不同：它不自请求 playurl，完全复用 yt-dlp 的 B站 DASH 提取。免登录音频这条两者等价。
- 依赖重量：核心链（httpx/yt-dlp/pysubs2/pydantic/dotenv/opencc）很轻；**1.2GB venv 里 torch(491MB)+mlx(206MB)+llvmlite(131MB)+scipy(83MB) 全是 `mlx-whisper` 拖进来的**，playwright(147MB) 是 PNG 导出拖的。若只取材不本地转写，纯 Python 依赖 <100MB。

## 4. 工程评估

- **代码质量：明显高于同星数段平均**。src 40 个模块共 ~11.7k 行，单文件最大 2,139 行（transcript_foundation.py），职责划分清晰（subtitles/asr/audio/download/platform/pipeline/storage 分层）；全类型注解 + ruff（E/F/I, line 100）；测试 43 个文件 ~9.4k 行。
- **实测测试套件**：`python -m pytest -q` → **634 passed / 18 failed / 53 skipped，23s**。18 个失败全部因未装 playwright chromium 或缺 pi 可执行文件（报告导出/容器类用例），字幕、ingest、ASR、存储、平台取材等核心逻辑全绿。
- **安全细节到位**：credential 脱敏（redaction.py 贯穿 asr.json/日志）、cookie 文件 0600、字幕 URL 域名白名单、签名 URL 不进日志。
- **可抽用组件盘点**：
  1. `xiaohongshu.py`（+`platform_media.py` 的 save_subtitles/read_platform_cookies）：小红书视频笔记取材完整方案，165 行，仅依赖 yt-dlp/httpx。**最值得抽**。
  2. `bilibili_subtitles.py` 的三块：WBI 签名、`/x/v2/subtitle/web/view` protobuf 解析（B站 AI 字幕 ai-zh，我们 bili-subtitle 若只做 CC 轨则这是补充）、`validate_cues`+coverage 垃圾字幕校验（可直接借给 bili-subtitle 当质检器）。模块 import 了 ingest 的异常类，抽时需带 2-3 个符号。
  3. `skills/video-report/`（SKILL.md + standard/brief 模板 + 5 个编辑参考）：纯 Markdown，官方本身就发布独立 ZIP 供任意 AI Agent 用；将来我们接 LLM 总结时可整体复用或参考其「按内容关系选 Profile/商业推广取舍」的规则设计。
  4. `download_worker.py` 的 backupUrl 重试与试看检测：思路可移植到我们的下载器。
- **耦合/负担**：报告生成强绑定 Pi RPC（Node 工具链 + 会话/隔离/追踪一整套）；SQLite 状态库 + runs 目录约定较重；`transcript_foundation.py` 2.1k 行的转写规范化对我们是超额供给。

## 5. 选型结论：**不整体采用，定向抽组件**

对照我们的管线规划（URL → 字幕优先〔bili-subtitle 已解决登录〕→ 无字幕本地 ASR 回退 → 可选 LLM 报告）与 Intel Mac 兼容约束：

1. **整体采用不成立**：
   - 核心差异点（LLM HTML 报告）必须 Pi agent + LLM key，我们无 key，且报告风格/模板才是其卖点而非管线本身；
   - B站字幕链路我们已有自研 bili-subtitle（登录态已解决），它的 SESSDATA 导入对我们是重复建设；
   - **本地 ASR 仅 MLX 后端，Intel Mac 不可用**（pyproject 里 mlx 是 Apple Silicon extra；Intel 上只剩百炼云端，又要 key）——与我们的 Intel 约束直接冲突，而 01 报告已验证 whisper.cpp/faster-whisper/SenseVoice 三个 Intel 可用引擎；
   - 1.2GB venv + Node + (报告还需 pi npm 包 + chromium) 的依赖面，对「取材+转写」这个小目标过重。
2. **抽组件（推荐，三件）**：
   - `xiaohongshu.py` 整模块：直接补上我们管线的小红书缺口（mediaV2 字幕优先、xsec_token 保留、匿名优先+cookie 回退）；
   - `bilibili_subtitles.py` 的 ai-zh protobuf 解析 + validate_cues/coverage 校验：作为 bili-subtitle 的「AI 字幕补充 + 字幕质检」增强；
   - `skills/video-report/` 模板与规则：留作未来 LLM 总结报告阶段的设计参考（零代码依赖）。
3. **转写实测顺带验证**：其 yt-dlp backupUrl 重试 + ffmpeg 校验链在无 key 环境完全可用，若短期想先跑通端到端，`transcribe --asr-backend mlx`（Apple Silicon 机器）是一条现成通路，但 Intel 侧应按 01 报告选 whisper.cpp 路线，不依赖本项目。

## 6. 本次新安装清单（供台账）

| 名称 | 版本 | 位置 | 大小 | 说明 |
|---|---|---|---|---|
| uv (Homebrew) | 0.13.9 | /opt/homebrew/Cellar/uv/0.13.9（17 files） | 34.3MB | 全局新增，唯一 brew 安装 |
| Python venv | CPython 3.12.16（uv 自管） | runs/eval-vra/venv | **1.2GB** | 含 mlx extra；大头 torch 491MB / mlx 206MB / playwright 147MB / llvmlite 131MB / scipy 83MB |
| 项目源码副本 | af73abd 快照 | runs/eval-vra/src | 13MB | vendors/ 只读，副本可弃 |
| HF 模型 | — | ~/.cache/huggingface（mlx-community/whisper-large-v3-turbo，1.6GB） | 0（本次未新下载） | 此前 asr-bench（01 报告）已缓存，本次实测直接命中 |

未安装（按需跳过、已验证不影响转写）：playwright chromium 浏览器、npm pi-coding-agent。
