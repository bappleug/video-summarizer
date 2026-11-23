# S2 交接文档 — bili-digest 云引擎 + doctor + summarize 组件 + 归位重构

> Phase 3 / S2 · 依据 [方案设计 v1.2](../方案设计.md) §0/§2/§5/§6 与 S1 实战反馈。本文档自包含。

## 目标

在已验收的 `tools/bili-digest/`（S1：B站 transcript 闭环）上完成五件事：**①组件归位重构 ②云转写引擎 ③doctor 体检 ④summarize 总结组件 ⑤validate 规则 4**。

## 前置事实

- S1 代码已入库（commit 9200695）：`src/bili_digest/{config,errors,audio,validate,pipeline,cli}.py + asr/{base,mlx_backend}.py + platforms/bilibili.py`，36 单测全绿。venv 在 `tools/bili-digest/.venv`（**复用，不要重建**；装包必须 `UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`）。
- API key 已就位：`~/.config/ai-tools/bili-digest/keys.env`（DEEPSEEK/ZHIPU/SILICONFLOW 三把，config.py 负责 parse；**值不打印不入库**）。
- 云 API 已实测过（research/04 §6）：硅基流动 `https://api.siliconflow.cn/v1/audio/transcriptions`，model `FunAudioLLM/SenseVoiceSmall`，multipart 直传 wav，300s 音频 8.85s；智谱 chat `https://open.bigmodel.cn/api/paas/v4/chat/completions` model `glm-5.3-flash`（注意 Flash 带思考、reasoning tokens 计入输出）；DeepSeek `https://api.deepseek.com/chat/completions` model `deepseek-chat`。
- **零新增依赖纪律**：LLM/云调用全部 stdlib urllib（multipart 自拼），不装 requests/openai。
- **B站字幕接口当日风控污染活跃**：开发测试用 fixture，不打真实字幕 API；需要端到端实测时用 P5（无字幕→ASR 路径，最多调 2 次）。

## 五个工作项

### ① 组件归位（D-011）
`asr/` → `transcribe/`（base.py、mlx_backend.py、cloud_sf.py），更新 import 与测试。`transcribe` 组件对外可独立使用：`from bili_digest.transcribe import ...`，输入 wav 路径，不依赖 platforms/pipeline。

### ② cloud_sf 云引擎（transcribe/cloud_sf.py）
- 端点默认 `api.siliconflow.cn`，401 时自动重试一次 `.com`（research/04 实测国内 key 必须 .cn）。
- 返回 `Transcript(engine="siliconflow-sensevoice", source="asr")`；>50MB 文件先 ffmpeg 压 mp3 再传（S2 先支持到 1h 内）。
- 错误分类（D-009 核心）：401→「key 无效或端点错」+控制台 URL；402→余额不足+充值入口；403→实名/权限；429→限流稍后重试；超时→网络。全部抛 `CliError` 带**修法指引文本**，不许裸抛状态码。
- `--engine auto` 更新：mlx 可用→本地；否则 cloud（无 key 时报「需配置 SILICONFLOW_API_KEY，运行 digest doctor 查看指引」）。

### ③ digest doctor（D-009）
体检并输出「能力 × 状态 × 下一步动作」表格（stdout）：
- 三把 key：存在性 + 实测 ping（硅基流动 `GET /v1/user/info` 查余额；智谱一条极短 chat 用免费 `glm-4-flash`；DeepSeek `GET /user/balance` 查余额，均为零成本接口）；
- 本地能力：mlx 可导入性（arm64）、ffmpeg、B站登录态（读凭据文件 + `bili status` 等价逻辑）；
- 单项失败不中断，最后汇总；有红项退出码 1。测试用 mock（不打真 API 也全绿；真 key 的实测作为手测验收项）。

### ④ summarize 组件（D-011 独立技能）
- `summarize/llm.py`：OpenAI 兼容 chat client（stdlib），provider 默认 zhipu/glm-5.3-flash、备 deepseek/deepseek-chat，从 keys.env 取 key；返回文本 + usage（tokens 与估算成本，费率常量注明查证日期）。
- `summarize/templates/`：中文 Markdown 报告模板，prompt 体系借鉴 research/05 §5：来源绑定（报告要点引用 transcript 时间戳 `[mm:ss]`）、ASR 谐音疑点消解提示、结构（一句话结论/要点/时间轴摘录/关键词）、输出长度约束。**1-2 次 LLM 调用**，不做多轮 agent。
- `summarize/report.py` + CLI：`digest <url> --summarize` 与 `digest report <run目录>`（对已有 transcript 补报告）。报告与 usage 写入 run 目录 report.md + meta.json。
- 独立可用：`from bili_digest.summarize import summarize_text`（输入任意文本+可选元信息 → 报告），脱离壳工程。

### ⑤ validate 规则 4：标题相关性（S1 缺口）
「完整但无关」字幕三规则拦不住（S1 实测 P5 拿到过新疆旅游字幕）。加规则 4：视频标题+简介的中文 bigram 与字幕文本的重叠率 ≥ 阈值（建议 15%，用实测校准；标题全是英文/数字时跳过本规则）。失败→warning→降级 ASR。构造测试样本：B站12周年标题 × 新疆旅游字幕（runs/s1-test/ 有真实样本可参考格式）必须被拦。

## 验收标准（逐条给证据）

1. `digest 'https://www.bilibili.com/video/BV1CV411s7jd?p=5' --summarize` 端到端出中文报告（GLM），meta.json 记录 usage/成本；`--provider deepseek` 可切换。
2. `--engine cloud` 对 P5 出 transcript，与本地 mlx 输出抽样对比内容一致（同视频同内容级别，无需算 CER，人读判断）。
3. `digest doctor` 在本机（三 key 齐）全绿；单测中 mock 坏 key/缺 key/401/402 各场景诊断文本正确。
4. 规则 4 测试：无关完整字幕被拦截并降级；正常字幕不受影响。
5. `pytest` 全绿（含新增）；`transcribe`、`summarize` 各有一个脱离 pipeline 的独立使用单测。
6. 全程 LLM 实际花费 <¥1（报告给出累计）。

## 边界（硬性）

- 只动 `tools/bili-digest/**`；不碰其他 tools/、vendors/、docs/、AGENTS.md；**不执行任何 git 命令**。
- 不新增 pip 依赖（stdlib only）；不重建 venv；不打印 key 值。
- B站真实 API 全天调用 ≤4 次（P5 为主）；遇到 412/污染如实记录停止。
- 完成报告：实现概览（模块行数）、验收逐条证据、LLM 花费、遇到的坑、（若有）新装清单。
