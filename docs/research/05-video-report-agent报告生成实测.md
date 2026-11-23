# video-report-agent 报告生成实测（LLM 报告能力补测）

> Phase 2 调研 · 2026-11-20 · 前置：[02-视频总结工具实测](02-video-report-agent实测.md)（转写路线已跑通，报告生成因无 key 未测）

- 被测能力：`video-report generate` 的「transcript → 结构化 HTML 报告」全流程（Pi RPC agent + video-report Skill）。
- 环境：沿用 `runs/eval-vra/`（src 副本 + uv venv --extra mlx），node v24.14.0（nvm）。测试视频不变：BV1CV411s7jd?p=5（B站12周年演讲 p05，311 秒、无字幕、复用 02 报告的 MLX 转写产物）。
- LLM：DeepSeek 官方 `deepseek-flash`（Pi provider `deepseek`），大陆直连。费用预算 ¥0.5，实测单次 $0.0694（≈¥0.50），已用满，智谱对比未跑。

## 0. TL;DR

- **接线一把过的门槛在 `.env` 位置**：VRA 的 `PROJECT_ROOT = Path.cwd()`，`.env` 必须放在**运行命令的 cwd**（本次为 `runs/eval-vra/.env`），放源码根 `src/.env` 无效——第一次运行即因此报 `No API key found for deepseek`（未产生 LLM 费用）。修正后一次跑通。
- **生成成功**：总耗时 **161.5 秒**（不含 ASR，transcript 自动复用），22 轮 assistant 消息 / 23 次模型请求，总消耗 59.4 万 tokens（其中 cacheRead 48.9 万），**估算成本 $0.0694 ≈ ¥0.50**。
- **报告质量超预期**：自拟观点标题、3 章节带时间区间回链、逐字引语框、独立 SVG 闭环图、105 个来源 unit 绑定全部有效；未开术语纠错时，ASR 谐音错字（杀掉镜头/赛威失马）被**上下文消解**为正确写法（杀青镜头/塞翁失马），低置信 UP 主名主动规避。
- **对自研总结模块：核心可抄资产是 Skill 的 prompt 体系**（关系→组件映射表、识别疑点消解规则、来源绑定契约、调用次数上限），而非 pi 依赖本身。详见 §5。

## 1. 接线步骤实录

### 1.1 安装 Pi（npm 全局）

```sh
npm install -g @earendil-works/pi-coding-agent@0.85.0   # 167 packages, 54s
pi --version                                            # 0.85.0
```

装到 nvm 目录（`~/.nvm/versions/node/v24.14.0/lib/node_modules/`），438MB，非系统污染。版本严格锁 0.85.0（README/docs 均写明）。

### 1.2 配置 key（.env）

按交接要求从 `~/.config/ai-tools/bili-digest/keys.env` 取 `DEEPSEEK_API_KEY` 写入（未回显、未入库）：

```dotenv
# runs/eval-vra/.env（chmod 600，runs/ 已 gitignore）
PI_PROVIDER=deepseek
PI_MODEL=deepseek-flash
PI_API_KEY=<DEEPSEEK_API_KEY>
PI_TASK_ISOLATION=local
ASR_BACKEND=mlx
HF_ENDPOINT=https://hf-mirror.com
```

### 1.3 第一次运行：失败（接线尝试 1/3，零费用）

```sh
cd runs/eval-vra && ./venv/bin/video-report generate '...BV1CV411s7jd?p=5' --asr-backend mlx
# transcribe 复用成功后，agent 阶段报错：
# No API key found for deepseek. Use /login to log into a provider via OAuth or API key.
```

**根因**：`src/video_report_agent/pi.py:28` `PROJECT_ROOT = Path.cwd()`，`cli.py:15` `load_dotenv(PROJECT_ROOT / ".env")`——`.env` 从**运行 cwd** 读取。我最初把 `.env` 放在 `src/`（文档「编辑仓库根目录的 .env」的说法对 `uv run` 场景成立，cwd 即仓库根；但用 venv 直跑且 cwd 在上一级时就会踩坑）。docs/cli.md 未提示 cwd 依赖。key 的传递路径：`PI_API_KEY` → 环境变量 → `pi` 子进程 `--api-key` 参数（pi.py:402，日志中已 redact）。

### 1.4 第二次运行：成功（接线尝试 2/3）

把同一份 `.env` 复制到 `runs/eval-vra/`（cwd）后重跑，全流程通过。`src/.env` 保留无害（该副本目录不被读取）。

## 2. 生成结果

### 2.1 运行概览

| 指标 | 数值 |
|---|---|
| run id | `0860fa3e9b4349f8aad010b14c2b9458`（runs/eval-vra/runs/） |
| 端到端耗时 | **161.5 s**（transcript 复用 02 报告的 run `9e296b0e…`，ASR 0 秒） |
| agent 会话 | 2 个：主报告 agent ≈132 s + 文风校订（style）agent ≈24 s |
| LLM 请求 | 23 次（22 轮 assistant 消息：20 轮 toolUse + 2 轮 stop） |
| 工具执行 | 24 次（读 transcript/Skill/模板、写 HTML、独立绘图等） |
| 首响应延迟 | min 303 / median 534 / max 998 ms（DeepSeek 大陆直连，很快） |
| 请求 payload | 累计 1.10 MB input bytes（23 次） |
| 最终状态 | `RENDERED`；PNG 导出失败（见 §4） |

### 2.2 Token 与成本（pi.events.jsonl 的 usage 记录，pi 按价目表估算）

| 项 | tokens | 估算成本 (USD) |
|---|---|---|
| input | 65,750 | $0.0197 |
| output | 38,948 | $0.0467 |
| cacheRead | 489,216 | $0.0029 |
| cacheWrite | 0 | $0 |
| **合计** | **593,914** | **$0.0694 ≈ ¥0.50** |

要点：**cacheRead 占总 tokens 的 82%**，但只花 $0.003（缓存价 ≈1/10）。Agent 多轮循环（22 轮共享同一份 Skill/模板/转写上下文）天然吃到 prompt 缓存，是这套流程便宜的关键。单轮峰值是第 12 轮「写整份 HTML」：output 18,252 tokens、$0.022，占全程 1/3。

### 2.3 report.html 结构（对照 README 宣称逐项核验）

文件 44.9KB（自包含、内联 CSS/JS，其中约 12KB 是被注释掉的模板说明——agent 复制模板时连注释一起带上了，渲染无影响，属小瑕疵）。渲染正文 7.7KB：

- **标题/导语**：主标题自拟观点题「观众笑了，快乐奖才成立」（非视频标题复述）；导语一段交代对象与主线；meta 行完整显示「Bilibili；哔哩哔哩弹幕网；《BILIBILI 12 周年演讲 p05…》；URL」——**宣称的来源行 ✓**
- **章节**：3 个编号章节（约 5 分钟口述串烧，符合 Skill 的详略自适应），每章 `h2` 右侧时间区间 `00:22–01:25` 等，**链接到原视频 `?p=5&t=22` 秒级定位 ✓**
- **逐字引语**：keyline 引语框 1 处，与转写 unit-000050/051（80–82s）**逐字一致**，署名「一位获奖UP主」✓
- **SVG 独立绘图**：1 张 820×500 弹幕反馈闭环图（`<figure data-report-diagram="true">`，绑定 15 个来源 unit，图注「观众弹幕如何回到创作者」），由独立的 generate_diagram 子任务产出、主会话只插占位符 ✓（README「Standard 中 Agent 按需发起绘图」，本篇恰好用上）
- **来源绑定**：18 个元素带 `data-source-units` 隐藏属性，共 165 个引用、105 个 unique unit id，**全部命中 transcript 的 175 个 unit（0 个无效 ID）**，覆盖率 60% ✓
- **页尾折叠区**：作品/作者/链接 + 「定位：unit-000006（00:22）…」6 个锚点映射 ✓
- **主题**：浅/深色跟随系统 + 手动切换按钮 ✓
- **术语校对**：本次**未开** `--term-correction`（需另配搜索服务 key）；但见 §3 的上下文消解效果
- **文风校订**：默认开启，独立 style agent 2 轮跑完（约 24 s）✓
- **PNG 长图**：**失败**。playwright chromium 未装（02 报告跳过了这步），`status.json` 记录 `image_error: Executable doesn't exist ... chrome-headless-shell`。CLI 无单独重导 PNG 的子命令，重跑 generate 会再花一次 LLM 费用，为守 ¥0.5 预算放弃。HTML 不受影响。

### 2.4 关键片段引用（正文第 2 章开头）

> 获奖稿件来自烦恼、意外和自己先笑出来（01:55–04:05）
> 被提到的获奖稿件题材很散，来历却相似：素材先在日常里出现，创作者觉得烦恼、好笑或意外，才把它拍成视频。
> ——随后 4 个小标题各带一个案例：「戴口罩遮不住下巴」「删QQ空间的旧言论」「MV花絮和杀青镜头」「一次失败跳水」。

写法上做了归纳合并（原文是十几位 UP 主逐个口述），保留全部事实性细节且每段可溯源。

## 3. 质量评价

### 3.1 优点

1. **来源可审计是最大亮点**。每段话、引语、图都有 unit 级绑定，页尾还给出锚点映射。抽查引语逐字一致、105 个 ID 零无效。做「AI 生成内容可信」这块，这套契约值得直接抄。
2. **ASR 错字的上下文消解有效**（未花一分钱搜索费）：转写「杀掉镜头」→ 报告「杀青镜头」（结合 MV/看片语境）、「赛威失马」→「塞翁失马」；低置信人名只保留「老番茄」「逍遥散人」（转写正确、知名度高），其余一律「一位UP主」，没有编造名字。「输轮」「阿部署」等无法消解的疑点句直接规避。这正是 SKILL 规则「识别疑点先结合上下文核对；非核心疑点省略；核心矛盾无法消解时只写各处共同支持的部分」的落地。
3. **标题和结构是「编辑」不是「摘要」**：观点式主标题、按问题重组章节、按案例归类（而非按 UP 主流水账）、时间区间回链，全部符合 README 宣称。
4. **快且稳**：161 秒出稿，23 次请求 0 重试 0 报错，首响应中位 0.5 秒。

### 3.2 不足

1. **模板注释残留**：12KB 的组件说明注释被原样带进最终 HTML（渲染无影响，体积虚胖 27%）。
2. **PNG 需要额外 100MB+ 的 chromium**，且失败不重试、无独立重导入口（只有重跑整条 generate）。
3. **`.env` 依赖 cwd 无文档提示**，跨目录直跑 venv 必踩（§1.3）。
4. 内容覆盖 60% 的 unit 属编辑取舍合理范围，但「瑞总瑞哥」「是男人就撑过五秒」的 UP 主等个体信息被合并，做「获奖名单」类检索场景会不够用——这是 Standard 精读定位使然，Brief/名单需求需另做。

## 4. 耗时与成本汇总

| 阶段 | 耗时 | 费用 |
|---|---|---|
| transcript（复用） | ~3 s | 0 |
| 主报告 agent（含并行独立绘图） | ~132 s | $0.068（含绘图） |
| 文风校订 agent | ~24 s | <$0.001 |
| HTML 检查/收尾 | ~2 s | 0 |
| PNG 导出 | 失败 | 0 |
| **合计** | **161.5 s** | **$0.0694 ≈ ¥0.50** |

对照：02 报告的 MLX 转写 12.5 s（纯 ASR）。即「转写+报告」全自研对标的单视频总成本 ≈ ¥0.5（flash 级模型），耗时 ≈3 分钟（5 分钟视频）。

## 5. 对自研总结模块的借鉴结论

按「值得抄 / 可选抄 / 不抄」分级：

### 5.1 值得直接抄

1. **Skill 分层 prompt 体系**（`skills/video-report/`）：105 行总纲（SKILL.md：输入契约、推广取舍、事实边界、修订流程）+ 模式文件（standard 94 行）+ **按内容关系二选一的 5 份 profile**（mechanism/procedure/evidence/argument/narrative 各 9 行，选定后才读）。入口短、按需加载，既省 token 又防串扰。自研模块可复刻为「总纲 + 题材 profile + 输出模板」三层。
2. **「关系→表达」决策表**（standard.md §用形式表达关系）：普通判断→正文、名称解释→dl、量级差距→bars、条件路径→branches、多对象多维度→table……每行带「必需字段」和「不适合用法与降级」。这是防止 LLM 乱排版的最有效手段——自研做 Markdown 报告可翻译成「关系→Markdown 块类型」表。
3. **来源绑定契约**：`data-source-units` 绑转写段 ID + 章节时间区间回链原视频 + 页尾锚点映射。自研 Markdown 版可用 `[u123](视频?t=142)` 行内引用实现同款，成本极低、审计价值大。
4. **识别疑点消解规则**（§3.2 实证有效）：不逐词搜查，只在「影响理解」时处理；自身知识只提候选、上下文先核对、消解不了就省略而不是猜。比开搜索求证便宜两个数量级。
5. **调用次数上限写进设计**：绘图最多 1 次、报告检查最多 2 次、术语搜索最多 3 次、修订只做一轮。预算确定性好，flash 级模型也能按住质量。

### 5.2 可选抄

- **多轮 agent 循环 + prompt 缓存红利**：22 轮读写循环靠 cacheRead 把 82% 的 token 打到 1/10 价格。自研若用支持 prompt cache 的 provider（DeepSeek/智谱都支持），「让 agent 自己读文件写文件」比「一把梭大 prompt」更便宜也更可控；但若一次调用就能出稿（简单总结场景），不必引入 agent 循环。
- **独立轻量校订会话**：主稿完成后用 2 轮小会话做文风校订（~24s、<0.1 美分），分工清晰。
- **transcript 复用机制**（`transcript_reused_from`）：同视频重跑不重复 ASR/下载，自研管线应有同款缓存键（视频 ID + 分辨率/模型）。

### 5.3 不抄

- **pi RPC agent 依赖**：438MB npm 包 + 0.85.0 版本锁定 + `.env` cwd 坑，依赖面重；自研用「一个 Python 循环 + provider SDK」即可复刻其价值（模型调用、读写文件、循环控制本来就在自己手里）。
- **自包含 HTML + 内联 CSS 模板**：模板工程精美（主题切换、响应式、组件库），但对我们「总结模块」的目标形态（Markdown 报告进 Obsidian/飞书）过重；其中「组件清单+降级规则」的思想搬到 Markdown 即可（5.1 第 2 条）。
- **独立绘图子任务**：效果好但流程复杂（独立会话、180s 时限、占位符回填）；自研首版可用 Mermaid 代码块替代。

### 5.4 一句话结论

**VRA 的报告生成验证了「flash 级模型 + 好的 Skill 约束 = ¥0.5/篇、3 分钟/篇 的可用报告」**；自研总结模块应以它的 SKILL.md/standard.md 为蓝本重写 prompt 体系（中文、领域适配 B 站），输出 Markdown + 来源行内引用，用「关系→块类型」决策表控排版，用「疑点消解」控事实边界——pi/agent 框架本身不需要。

## 6. 新增安装清单（供台账登记）

| 项 | 版本 | 位置 | 大小 |
|---|---|---|---|
| npm 包 `@earendil-works/pi-coding-agent` | 0.85.0（全局，nvm 内） | `~/.nvm/versions/node/v24.14.0/lib/node_modules/@earendil-works/pi-coding-agent` | 438 MB（含依赖 167 包） |
| `.env`（含 DEEPSEEK_API_KEY） | - | `runs/eval-vra/.env`（及 `src/.env`，均 chmod 600，runs/ 已 gitignore，不入库） | 166 B |

无其他系统级安装；playwright chromium 仍未装（本次 PNG 因此未导出）。测试产物在 `runs/eval-vra/runs/0860fa3e9b4349f8aad010b14c2b9458/`（report.html、pi.events.jsonl、generate_deepseek.log 等），可自主清理。
