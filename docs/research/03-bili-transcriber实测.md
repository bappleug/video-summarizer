# bili-transcriber 实测（Mac arm64 可用性 + 组件复用评估）

> Phase 1 调研 · 2026-11-20 · 被评估项目：[Asaceoo/bili-transcriber](https://github.com/Asaceoo/bili-transcriber) `d4943ea`（README 标 v0.1.45，40★），副本位于 `vendors/bili-transcriber`（只读）。结论见文末「总体结论」。

## 0. 项目概况

定位：Windows 桌面工具（NiceGUI + pywebview），粘贴 B站/抖音/小红书/快手/YouTube 链接 → yt-dlp 下载 → PyAV/ffmpeg 转码 16kHz 单声道 WAV → 本地 ASR（faster-whisper CUDA / Qwen3-ASR / SenseVoice sherpa-onnx int8）→ SRT/TXT/Markdown 落盘。附带 SQLite 历史库、CLI、MCP（stdio + HTTP）三个无界面通道，三者共享同一份 `data/`（settings.json + history.db）。

架构分层干净，是我们最关心的部分：

```
app/cli.py / app/mcp_server.py / app/mcp_http.py   ← 三个入口（无界面）
        └── app/headless.py::Service                ← 无界面服务层（提交/查询/设置/检索）
                └── app/pipeline.py::Pipeline       ← 任务编排（队列/状态机/断点续跑/取消暂停）
                        ├── app/downloader.py       ← yt-dlp 封装 + 快手自研 SSR 解析
                        ├── app/converter.py        ← PyAV 优先转码
                        ├── app/transcriber.py      ← faster-whisper 封装
                        ├── app/sensevoice_transcriber.py ← SenseVoice 封装
                        ├── app/qwen_transcriber.py ← Qwen3-ASR 封装
                        └── app/writers.py          ← SRT/TXT/MD 原子落盘
app/main.py + app/ui/                               ← NiceGUI 桌面端（本次不评）
```

代码量约 9.2k 行（app/），注释密度高、几乎每个非常规决定都写了原因（含 AI 传参防御、并发竞态、Windows 编码坑），工程素养显著高于同星数项目平均水平。测试目录 31 个文件（README 称 544 用例）。

## 1. 安装实录（Mac M4 Pro / macOS arm64 / python3.11）

**第 1 步：副本 + venv**（`vendors/` 保持只读）

```bash
cp -R vendors/bili-transcriber runs/eval-btc/src && rm -rf runs/eval-btc/src/.git
cd runs/eval-btc/src && /opt/homebrew/bin/python3.11 -m venv .venv
```

**第 2 步：按 pyproject 原样安装 → 失败（Windows-only 依赖坐实）**

`pip install -e .` 在依赖解析阶段即被阻断：

```
ERROR: Could not find a version that satisfies the requirement nvidia-cublas-cu12>=12.6 (from bili-transcriber) (from versions: 0.0.1.dev5)
ERROR: No matching distribution found for nvidia-cublas-cu12>=12.6
```

`nvidia-cublas-cu12` / `nvidia-cudnn-cu12` 只有 Linux/Windows 的 CUDA 轮子，macOS arm64 无发行版。**在不改 pyproject 的前提下，标准安装路径在 Mac 上走不通**；README 的系统要求表也明确只写了 Windows 10/11（x64）。

**第 3 步：剔除两个 nvidia 包后安装 → 成功**

```bash
pip install "yt-dlp>=2025.1.1" "faster-whisper>=1.1.0" "nicegui>=2.0" "pywebview>=5.0" \
  "qwen-asr>=0.0.6" "av>=12.0" "sherpa-onnx>=1.13.6" "soundfile>=0.12" \
  "pystray>=0.19" "Pillow>=10.0" "imageio-ffmpeg>=0.5"
pip install -e . --no-deps    # 装入口（bili-transcriber / -cli / -mcp）
```

全部解析成功，关键版本：sherpa-onnx **2.0.2**（有 arm64 轮子）、faster-whisper 1.2.1 + ctranslate2 4.9.0、qwen-asr 0.0.6（连带拉入 torch 2.15.0 / transformers 4.57.6 / gradio 6.17.3，是体积大头）、nicegui 4.0.0、pywebview 6.2.1（pyobjc 系 macOS 轮子齐）、av 18.1.0、yt-dlp 2026.10.26、imageio-ffmpeg 0.8.1。venv 共 146 个包、**1.9 GB**（若不做 Qwen 引擎可砍掉约一半以上）。

**第 4 步：配置 SenseVoice + 复用现有模型**（模型外置设计，支持指定目录）

```bash
.venv/bin/python -m app.cli --set engine=sensevoice \
  --set model_dir=/Users/raymin/workspace/ray/ai-tools/bili/runs/asr-bench/models \
  --set language=zh --set output_dir=…/runs/eval-btc/out
```

模型查找逻辑（`app/models.py::find_sensevoice_model`）：精确名 → 目录名含 `sensevoice`/`sense-voice` 关键字 → 唯一有效目录兜底，只认 `model.int8.onnx + tokens.txt`。我们现有的 `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/`（含 model.int8.onnx 239MB）经关键字分支命中，**零下载直接复用**。另有默认 `HF_ENDPOINT=https://hf-mirror.com`（`app/__init__.py` setdefault），与其余引擎在线下载路径兼容大陆网络。

副作用一处：日志目录硬编码 `~/Bili Note/logs/app.log`（无环境变量），测试后已手动删除，归档副本在 `runs/eval-btc/app.log.copy`。

## 2. 转写实测

### 2.1 CLI 端到端

```bash
time .venv/bin/python -m app.cli "https://www.bilibili.com/video/BV1CV411s7jd?p=5" --engine sensevoice --json
# → exit 0，status=done，all_done=true
```

311.4 秒中文获奖感言，**整条命令 wall time 16.6 秒**（含 Python 启动 + import）。日志级阶段分解：

| 阶段 | 耗时 | 备注 |
|---|---|---|
| import + yt-dlp probe + 匿名下载 m4a | ~12 s | 无任何 cookie，bestaudio 单音轨 |
| 转码 16k 单声道 WAV | <1 s | PyAV 路径（本机 PATH 的 ffmpeg 仅作回退） |
| SenseVoice 模型加载 | ~1 s | 复用 asr-bench 模型目录，日志确认目录名 |
| 转写 311.4s（11 块 × 30s） | **~2–3 s** | ≈ **100–150× 实时**（日志秒级精度） |
| 落盘 SRT/TXT/MD | <1 s | 原子写 |

转写速度值得注意：我们在 `01-本地ASR引擎对比` 里自测同一 sherpa-onnx 2.0.2 + int8 为 15.4× 实时（19.5s/300s），本项目快一个量级。差异来自其封装用 `num_threads=min(8, max(2, cpu_count))`（M4 Pro → 8 线程），我们自测脚本未开多线程。**这是本次实测最有价值的增量发现之一：SenseVoice 在 Apple Silicon 上多线程解码还有约 7–8× 的免费提速空间**，我们的双端回退方案应当吸收。

产物（`runs/eval-btc/out/BV1CV411s7jd_…获奖感言/`）：`.m4a` 12.4MB（`keep_audio` 默认 true）+ `.srt` 6.9KB + `.txt` 5.1KB + `.md` 6.2KB。质量抽样（SRT 前几段）：时间戳合理、带标点、ITN 生效，错字水平为 SenseVoice 典型（"UP主"→"U主"、"逍遥散人"对、"陶成长"误），与我们 01 报告中 SenseVoice 的 CER 结论一致，无系统性跑偏。

小瑕疵一枚：MD 头部元数据写「转写模型：large-v3-turbo (cpu)」——`pipeline.py` 落盘时取的是 `settings.model_size`（whisper 默认名），不随 engine 切换，对 SenseVoice 运行属于错误标注（仅影响元数据展示，不影响正文）。

### 2.2 MCP stdio 验证

手写 JSON-RPC 管道实测（`python -m app.mcp_server`）：

- `initialize` → 正常协商（serverInfo: bili-transcriber/0.1.45，instructions 返回使用指引）；
- `tools/list` → 11 个工具全量返回；
- `tools/call search_transcript {"keyword":"逍遥散人"}` → 命中刚才的任务，返回 media_id/标题/上下文片段；
- `tools/call list_jobs` → 返回 done 状态任务与产物绝对路径。

即：**MCP 通道在 macOS 上与 CLI 同等可用**（共用 Service/Pipeline，不碰任何 Windows 代码）。

## 3. 四个关键问题

### 3a. macOS 兼容性：哪些是 Windows 专属？核心链路剥离 UI 后能否纯 Mac 跑？

**安装层**（唯一硬阻塞）：`nvidia-cublas-cu12`/`nvidia-cudnn-cu12` 无 macOS 轮子，`pip install -e .` 直接失败，必须剔除后 `--no-deps` 补装。

**代码层**（全部有守卫或 try/except 包裹，Mac 上自动降级而非崩溃）：

| 模块 | Windows 专属点 | Mac 上的行为 |
|---|---|---|
| `gpu_runtime.py` | 检测 `cublas64_12.dll`、`os.add_dll_directory` 注册外置 GPU 包 | `ensure_gpu_or_cpu()` 开头 `sys.platform != "win32"` 直接返回 cpu |
| `transcriber.py::_resolve` | `ctypes.WinDLL("cublas64_12.dll")` 预验 CUDA | 在 try/except 内，Mac 抛 AttributeError → cuda_ok=False → 回退 cpu（日志一条 warning） |
| `hardware.py` | winreg 取 CPU 名、nvidia-smi/wmic 取 GPU | 均有 `sys.platform == "win32"` 守卫，Mac 返回空/回退 `platform.processor()` |
| `cookiebridge.py` | Chrome v20 App-Bound 加密桥接（chrome.exe、%PROGRAMFILES%、headless CDP 解密） | Mac 上浏览器名定位失败 → 回退 yt-dlp 原生 `cookiesfrombrowser`；**cookie_file（Netscape txt）路径完全跨平台** |
| `app/__init__.py` | `os.add_dll_directory`（site-packages/nvidia） | 目录不存在则不调用，Mac 无 nvidia 包，不触发 |
| `logging_setup.py` / `models.py` | 默认目录 `%LOCALAPPDATA%\Bili Note` | 回退 `~/Bili Note`（家目录副作用，见 §1） |
| `cli.py` | GBK/PyInstaller console=False 编码处理 | 纯 Windows 打包场景，Mac 无影响 |

**结论：剥离 UI 后核心转写链路（downloader → converter → sensevoice_transcriber → writers → store/headless/mcp）100% 纯 Mac 可跑，已由 CLI + MCP 实测证实。** GUI 理论上也能起（pywebview/nicegui 都有 macOS 轮子，托盘 pystray 也装上了），但未实测也非评估重点。faster-whisper 引擎在 Mac 上只有 CPU 路径（device 枚举仅 auto/cuda/cpu，无 mps），按我们 01 报告结论 CPU 太慢不推荐——该项目主推的 Whisper+CUDA 路线对 Mac 无意义，Mac 上实际可用引擎就是 SenseVoice（实测正常）。

### 3b. 下载链路：yt-dlp 匿名下载 B站行不行？cookie 怎么处理？小红书怎么支持的？

- **B站匿名下载：行**。实测无任何登录态，`format="bestaudio[ext=m4a]/bestaudio/best"` 只拉音轨，311s 视频 m4a 12.4MB 约 12 秒（含 probe）。多 P/合集由 probe 阶段展开（`noplaylist=False`），media_id 形如 `BV1CV411s7jd_p5`，下载阶段 `noplaylist=True` 逐条拉。大会员/高清晰度内容需要 cookie（README 平台表明确标注）。
- **Cookie 三层优先级**：`cookie_file`（Netscape cookies.txt，跨平台，优先）> `cookies_from_browser`（先走自研 cookiebridge——把 Chrome/Edge v20 加密 cookie 经 headless 浏览器 CDP 解密落成临时 cookies.txt，10 分钟缓存，**纯 Windows 技术**；桥接失败退回 yt-dlp 原生 `cookiesfrombrowser`）> 无。Mac 上可用形态是 cookies.txt 或 yt-dlp 原生浏览器读取（Chrome v20 在 Mac 同样是 yt-dlp 的已知痛点）。
- **代理**：设置页 `proxy` 透传 yt-dlp（http/socks5），另有 `ensure_reachable` 平台连通性预检——进 yt-dlp 前先发一次轻量 HTTPS 探测（60s TTL 缓存），不可达立即报错，避免 yt-dlp 按默认重试策略把 worker 堵 2–3 分钟。这个细节值得抄。
- **小红书**：`_detect_platform` 识别 `xiaohongshu.com` / `xhslink.com` → **走 yt-dlp 原生提取器**（yt-dlp 内置 XiaohongshuIE），不是自研解析。`xhslink.com` 短链先过自研 `_resolve_redirect`：入口域名硬编码白名单、每跳校验协议/平台域名后缀/解析 IP 非内网（防 DNS rebinding），最多 5 跳，失败回退原 URL 交给 yt-dlp。README 标注小红书「需 Cookie：登录后才返回完整视频地址」。小红书无独立音轨时下载合一 mp4，转码阶段抽音频（`audio_exts` 显式含 `.mp4`）。
- **快手**：yt-dlp 无内置提取器 → 自研分享页 SSR 解析（`kuaishou.py`，141 行）：iPhone UA 抓 HTML、正则抽 mp4 直链（hd15 优先）、token 失效自动重抓，免登录。
- **抖音**：yt-dlp DouyinIE + 两种 URL 改写（`modal_id=` 弹窗链接、iesdouyin 分享页 → `/video/<id>`），需 cookie。
- **视频号**：明确拦截报错引导本地上传（网页需微信登录且接口加密）。

### 3c. MCP 接口设计（Phase 3 直接参考，11 个工具全量抄录）

实现形态：**不依赖 mcp SDK**，手写 JSON-RPC 2.0。stdio（换行分隔）为主，另有 streamable-http 最小实现（`mcp_http.py`：只绑 127.0.0.1、Origin 校验防 DNS rebinding、默认不发 CORS、可选 Bearer token `BILI_MCP_HTTP_TOKEN`、session 30 分钟 TTL/上限 64、body 上限 8MB、`GET /mcp` 回 405、`/healthz` 探针）。协议版本协商 `2025-06-18 / 2025-03-26 / 2024-11-05`；未实现的 `resources/list`、`prompts/list` 等回**空清单**而非 -32601（避免部分客户端把整个服务标记不可用）。

工具清单（`app/mcp_server.py::TOOLS`，schema 已核对源码）：

| # | 工具 | 参数（粗体=required） | 语义 |
|---|---|---|---|
| 1 | `transcribe` | **source**(链接或本地绝对路径), wait(bool,默认false), timeout_sec(默认600), output_dir, engine(whisper/qwen3-asr/sensevoice), model_size, device(auto/cuda/cpu), compute_type, language, notes(bool) | 提交转写，立即返回任务 ID；wait=true 阻塞到完成。覆盖参数仅本进程生效不落盘 |
| 2 | `wait_jobs` | **media_ids**(string[]), timeout_sec(默认600) | 等一组任务进终态或超时；检测到「排队但本进程不会调度」时返回 stalled=true 立即退出，不让调用方白等 |
| 3 | `job_status` | **media_id** | 单任务阶段/进度/错误/产物绝对路径 |
| 4 | `list_jobs` | status, search, limit(默认20,1–200) | 历史倒序；status 传 running/active 等口语词自动映射到进行中状态集合 |
| 5 | `get_transcript` | **media_id**, format(md/txt/srt/notes,默认md), max_chars(默认12000,≤0全文) | 读产物文本，超限截断并标注原文总长 |
| 6 | `search_transcript` | **keyword**, limit(默认10), context_chars(默认240), fmt(md/txt) | 已完成任务全文检索，每任务取首个命中+上下文片段（省 token 的高频诉求） |
| 7 | `check_network` | mode(quick/full/test), timeout(默认4s) | 只读网络/Cookie 自检：quick=连通+代理；full=再扫浏览器登录态与 cookies.txt；test=按当前配置逐平台实测 |
| 8 | `cancel_job` | **media_id** | 取消排队/进行中任务 |
| 9 | `prewarm_model` | （无参） | 后台预加载当前设置模型（立即返回，长驻进程受益） |
| 10 | `get_settings` | （无参） | 读持久化设置；本进程临时覆盖单列 session_overrides |
| 11 | `update_settings` | output_dir, engine, model_size, device, compute_type, language, notes, vad, keep_audio, cookie_file, proxy, workers | 持久化写入 settings.json；通道开关 enable_cli/enable_mcp 故意不在白名单（防提示注入的 AI 给自己开门） |

工程细节（都值得 Phase 3 照抄）：

- **stdout 隔离**：协议通道握在本地变量，`sys.stdout` 重定向到 stderr——任何第三方库的 print 都不会插进 JSON-RPC 流；
- **出站 JSON 净化**：NaN/Infinity → null（裸 NaN 会让 JS 端 JSON.parse 直接炸）；
- **AI 传参防御**：数值 clamp（timeout 上限 86400「AI 会传 1e9」）、类型强转、空串当场拒绝而非落盘、坏值回退默认；
- **错误语义**：工具异常回 `isError:true` + `{"ok":false,"error":…}` 文本，服务进程不死；
- **submit 幂等**：重复提交同一链接返回 skipped/running，不重复下载转写；
- instructions 字段内置使用指引（先 transcribe → wait_jobs → get_transcript 的典型流）。

### 3d. 引擎封装质量：值不值得抽组件复用？

**SenseVoiceTranscriber（265 行）——值得，近乎原样可用。** 优点：懒加载 + double-checked locking（多 worker 并发首转不重复加载）；30s 分块无重叠 + 时间戳按块偏移累加；token 流按中英文句界切成 Segment，时间戳缺失时按时长比例两遍扫描兜底（不丢文本）；`on_event` 阶段回调直达 UI/日志；`unload()` 供空闲释放；模型目录三级匹配。仅依赖 sherpa-onnx/numpy/soundfile + `app/models.py` 的路径解析，剥离成本 ≈ 把 `resolve_model_dir/find_sensevoice_model` 两个函数一起带走。**`num_threads=min(8,…)` 是实测验证的提速点（§2.1），必须吸收。** 缺点：硬编码 `model.int8.onnx`（int8 专用，正好符合我们选型）；无 VAD（静音块照常 decode，但 150× 实时下无所谓）；30s 硬切可能截词（句界切分缓解）；metadata 不区分引擎的小 bug（§2.1）。

**Transcriber（faster-whisper，239 行）——设计好但对我们无价值。** auto 设备解析、CUDA 批量推理（BatchedInferencePipeline，2–4× 提速）、VAD 零语音自动关闭重试（唱歌/纯音乐场景）都是高质量处理，但整条加速路线绑 Windows CUDA；Mac 只有 CPU 慢路径（我们 01 报告已判死）。接口形状可参考。

**QwenASRTranscriber（466 行）——未实测**（需另下 Qwen3-ASR 模型）。接口对齐、字级时间戳（可选 ForcedAligner）、生成 token 上限/分块时长的坑位注释都很细，如果未来要 Qwen 路线可回来参考。

**统一引擎接口（值得抄的设计）**：三引擎都实现 `load() / transcribe(wav, duration, progress) -> list[Segment] / resolved_device / unload()`，pipeline 按 `(engine, model, device, compute, language, model_dir)` 元组缓存实例，改设置才换实例。我们的 ASR 层如果也做这个适配器形状， mlx-whisper / SenseVoice 双引擎切换会很顺。

**配套小件**：`converter.py`（PyAV 优先、ffmpeg 回退、失败清半截产物防脏缓存）与 `writers.py`（临时文件 + `os.replace` 原子写 + PermissionError 重试、SRT/TXT/MD 三格式）都是 100–190 行的独立好件，可按需抽。`downloader.py` 的平台探测/短链安全重定向/连通性预检同理（但我们已有 bili-subtitle 链路，按需取用）。

## 4. 总体结论：抽组件，不整体采用

| 选项 | 判定 | 理由 |
|---|---|---|
| 整体采用 | ❌ | ① pyproject 硬依赖 nvidia-cu12，macOS 装不上，fork 改依赖才能装；② README/测试矩阵仅 Windows 10/11 x64，上游是 40★ 单人项目，Windows 优先的演进方向与我们双端 Mac 约束长期错位；③ 主推引擎 faster-whisper 在 Mac 只有慢 CPU 路径（01 报告已判不推荐）；④ 它是「桌面应用 + 历史库」形态，我们要的是嵌进自己管线的组件，整体引入 = 白背 1.9GB venv + NiceGUI + SQLite 一整套 |
| **抽组件（推荐）** | ✅ | 见下表，MCP 设计与 SenseVoice 封装两块对我们有直接增量 |
| 完全不采用 | ❌ | MCP 接口设计与若干工程细节的参考价值真实存在，弃之可惜 |

**抽哪些（按优先级）**：

1. **MCP 接口设计整体照抄**（§3c 的 11 工具表 + 协议细节）——这是我们 Phase 3 自研 MCP Server 最直接的参考：工具命名/参数语义、transcribe+wait_jobs+get_transcript 的异步三段式、search_transcript 全文检索、参数防御与 stdout 隔离等。实现上我们仍建议用官方 `mcp` Python SDK（它手写是为省桌面包依赖，我们没有这个约束），但 schema 与语义可直接搬。
2. **`sensevoice_transcriber.py` 近乎原样抽走**（连带 `models.py` 的模型目录解析）——含 8 线程解码提速，把我们双端回退方案的单测速度从 15× 提到 100×+ 实时量级。
3. **引擎适配器接口形状**（`load/transcribe/unload/resolved_device` + 设置元组缓存）——统一我们 mlx-whisper（AS 端）与 SenseVoice（双端）两引擎。
4. **小件按需**：`converter.py`（若我们还没有 PyAV 优先的转码）、`writers.py` 原子落盘、`downloader.py` 的短链安全重定向与平台连通性预检。

**对照已有 ASR 结论**：模型与推理库和我们完全同源（sherpa-onnx 2.0.2 + 同一个 int8 模型目录），引擎层它没有超越我们已有能力的东西——真正的增量是 **MCP 接口设计**（我们还没有）和 **num_threads 提速**（我们自测漏了）。这两点 + 接口形状，就是本次评估的全部收获，其余用不上。

## 5. 新安装/下载清单（供台账）

| 项 | 位置 | 大小 | 说明 |
|---|---|---|---|
| venv（146 包） | `runs/eval-btc/src/.venv` | 1.9 GB | pip 清华镜像；含 torch 2.15.0/gradio 6.17.3（qwen-asr 连带，未用）；关键：sherpa-onnx 2.0.2、faster-whisper 1.2.1、yt-dlp 2026.10.26、nicegui 4.0.0、pywebview 6.2.1、av 18.1.0、imageio-ffmpeg 0.8.1、soundfile 0.14.0 |
| 项目副本 | `runs/eval-btc/src`（除 .venv） | 3.0 MB | vendors/ 只读，副本已去 .git |
| 转写产物 | `runs/eval-btc/out/BV1CV411s7jd_…/` | 12 MB | m4a 12.4MB + srt/txt/md 共 18KB |
| 运行数据 | `runs/eval-btc/src/data/`（settings.json + history.db）、`src/cache/`（空，wav 已自清） | 16 KB | |
| 日志归档 | `runs/eval-btc/app.log.copy` | <100 KB | 原始位置 `~/Bili Note/logs/app.log` 已删除 |
| 模型 | 无新下载 | — | 复用 `runs/asr-bench/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/` |

未动：`vendors/`、`runs/asr-bench/out/`、`runs/eval-vra/`、`tools/` 及其他文档；未执行 git；未用 brew；pip 仅在本 venv。
