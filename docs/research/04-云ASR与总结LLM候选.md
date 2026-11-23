# 云 ASR 降级路线与总结 LLM 候选（纯桌面调研）

> Phase 1 调研 · 2026-11-20 · 纯 Web 检索核对，未实测调用 · 结论先行，细节在后

## 0. 结论（两个推荐）

### A. 云 ASR 降级路线（老 Intel Mac 跑不动本地时的备胎）

| | 推荐方案 | 理由摘要 |
|---|---|---|
| **默认** | **硅基流动（SiliconFlow）SenseVoice-Small** | **免费模型（¥0）**；`POST /v1/audio/transcriptions` 兼容 OpenAI 协议，**multipart 直传本地音频文件，不需要公网 URL**——B站视频本地抽出的音频可直接上传；国内厂商大陆直连；限制为单文件 ≤50MB、时长 ≤1 小时（20-30 分钟视频抽 16kHz mono 音频约 27MB，天然满足） |
| **备选** | **阿里云百炼 paraformer-v2** | **每月免费 10 小时**（每月 1 日自动发放），超出仅 **¥0.288/小时**；大陆直连稳定。代价：录音文件识别 API **只收公网 URL（file_urls），不支持本地文件直传**，需先把音频传到 OSS 等公网存储，流程多一步 |
| 排除 | Groq / OpenAI | 两者**大陆均不可直连**。OpenAI 官方明确中国大陆不在支持地区（2024-07 起封禁不支持地区的 API 调用，有封号风险）；Groq 域名被墙且对大陆 IP 有区域限制（社区有封号案例）。走代理违背「网络可达性是硬指标」，均不纳入 |

与本地引擎的衔接备注：降级路线得到的仍是纯文本 transcript，下游管线不变。SenseVoice-Small 云端版与本地 sherpa-onnx SenseVoice 同源模型（本地实测见 `01-本地ASR引擎对比.md`），输出风格一致（自带标点、带语气词），对总结质量无额外差异。

### B. 总结 LLM（transcript → 报告）

| | 推荐方案 | 理由摘要 |
|---|---|---|
| **默认** | **智谱 GLM-5.3-Flash**（bigmodel.cn） | 单次报告（10k 入 / 3k 出）约 **¥0.016/次**，100 次/月约 ¥1.6；无高峰浮动价；1M 上下文；国产平台直连稳定。另平台有 **GLM-4.7-Flash / GLM-4-Flash 永久免费**档，开发调试和「免费兜底」都够用 |
| **备选** | **DeepSeek 官方 deepseek-flash** | 单次约 **¥0.022（空闲时段）/ ¥0.044（高峰时段）**；新用户赠送约 ¥10 余额（第三方信源，官方页面未明示，见 §4.3），够测几百次；最低充值 ¥1。缺点：分高峰/空闲两档价，高峰翻倍 |

两家的免费资源都足够「测试几十次报告生成」：智谱注册送 2000 万 tokens 组合包 + Flash 档永久免费；DeepSeek 送约 ¥10（≈ 空闲档 flash 450 次报告）。

---

## 1. 调研范围与信源口径

- **查证日期：2026-11-20**（下文所有价格/政策均为此日期核对值；标注「未能核实」处为查不到确切数字，禁止引用）。
- 信源以**官方文档/价格页**为主（siliconflow.cn、help.aliyun.com、docs.bigmodel.cn、api-docs.deepseek.com、help.openai.com）；注册赠送、社区反馈等无官方页面的信息用第三方信源并明确标注。
- 纯桌面调研：未注册、未实测调用。各家 API 实际转写质量/延迟未验证。

## 2. A 部分：云 ASR 降级路线明细

### 2.1 硅基流动（SiliconFlow）SenseVoice-Small

| 项目 | 核对结果 | 来源 |
|---|---|---|
| 现价 | **免费（¥0）**，计价单位为 UTF-8 字符（每千字符）；同档还有 TeleAI/TeleSpeechASR 等免费 ASR；付费 ASR 如 Qwen3-ASR-1.7B ¥0.00022/秒（≈¥0.79/h）供升级选择 | [siliconflow.cn/pricing](https://siliconflow.cn/pricing)，查证 2026-11-20 |
| 接口 | `POST https://api.siliconflow.com/v1/audio/transcriptions`，OpenAI 兼容，multipart/form-data，必填 `model` + `file`。**⚠ 实测修正（§6.1）：国内账号 key 必须用 `api.siliconflow.cn` 端点，`.com` 返回 401** | [API 文档](https://api-docs.siliconflow.cn/docs/api/audio-transcriptions-post)，查证 2026-11-20 |
| 本地文件直传 | **支持**。`file` 参数为「音频文件对象（not file name）」，二进制上传；**不需要文件先公网可下载** | 同上 |
| 文件大小/时长上限 | **≤50MB、≤1 小时**（官方 API 文档明示）；超限需压缩或分段 | 同上 |
| 音频格式 | 文档未列清单，仅示例 `audio.mp3`（其余格式**未能核实**）；建议用 ffmpeg 抽成 mp3/wav 常规格式 | 同上 |
| 注册门槛 | 官网 cloud.siliconflow.cn **手机号/邮箱注册**；**实名认证（支付宝扫码，个人）后解锁新用户赠送额度（¥14）及推荐官权益**；API Key 在控制台「API 密钥」页创建，仅显示一次 | [实名认证文档](https://api-docs.siliconflow.cn/docs/userguide/faqs/authentication)、[知乎上线公告](https://zhuanlan.zhihu.com/p/714291231)，查证 2026-11-20 |
| 免费额度 | SenseVoice-Small 本身是免费模型（长期免费，平台保留调价权利）；另有新用户 ¥14 赠送额度（用于付费模型） | [siliconflow.cn/pricing](https://siliconflow.cn/pricing)、[知乎](https://zhuanlan.zhihu.com/p/714291231)，查证 2026-11-20 |
| 速率限制 | 账号分级：L0（新用户）RPM 1,000 / TPM 40,000；免费模型另有固定限速（公告口径：免费模型 RPM 最高 1000、TPM 上限 50,000）；单次转写一个文件，对本工具「一条视频一次调用」的场景完全够用 | [速率限制文档](https://docs.siliconflow.com/cn/userguide/rate-limits/rate-limit-and-upgradation)，查证 2026-11-20 |

**对本工具的适配性**：流程最短——ffmpeg 从 B站视频抽出音频（30 分钟 16kHz mono wav ≈ 27MB，或压成 mp3 更小）→ multipart 直传 → 拿 text。无 OSS 中转、无任务轮询、零费用。唯一工程注意点：>1h 的超长视频需先切片。

### 2.2 阿里云百炼 paraformer-v2 / qwen filetrans

| 项目 | paraformer-v2 | qwen3-asr-flash（及 -filetrans） | 来源 |
|---|---|---|---|
| 现价 | **¥0.00008/秒 = ¥0.288/小时**（华北2-北京；按输入音频秒数计费，输出不计费） | **¥0.00022/秒 ≈ ¥0.792/小时**（华北2-北京；新加坡地域 ¥0.00026/秒） | [百炼模型价格页](https://help.aliyun.com/zh/model-studio/model-pricing)，查证 2026-11-20 |
| 免费额度 | **每月 36,000 秒（10 小时），每月 1 日 0 点自动发放，有效期 1 个月**（月月刷新） | 36,000 秒（10 小时），有效期 90 天（自开通/模型发布起算，仅华北2地域有） | 同上 |
| 文件提交方式 | **仅公网 URL**：`file_urls`（list，单次请求仅 1 个 URL，HTTP/HTTPS）；FAQ 明示「不支持 Base64、不支持二进制流、也不支持直接识别本地文件」；OSS 上 RESTful API 可用 `oss://` 临时 URL（48h 有效） | 异步 filetrans 同样**仅公网 http(s) URL**；**同步 qwen3-asr-flash 支持 DashScope SDK 读本地文件（file://），但限单文件 ≤10MB、时长 ≤5 分钟**——对 20-30 分钟视频不够用 | [paraformer Python SDK 文档](https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-python-sdk)、[qwen3-asr-flash-filetrans 模型页](https://help.aliyun.com/zh/model-studio/qwen3-asr-flash-filetrans)、[Qwen ASR API 参考](https://www.alibabacloud.com/help/zh/model-studio/qwen-asr-api-reference)，查证 2026-11-20 |
| 调用流程 | 异步：提交任务（PENDING 排队，通常数分钟内）→ 轮询/阻塞等待 → 结果与 URL 下载链接有效期 24 小时；SDK 一步式 `client.audio.asr.transcribe` 可阻塞到完成 | 同为异步任务（不支持 SSE 流式）；qwen3-asr-flash 本体为同步/流式 | 同上 |
| 文件规格 | 说话人分离场景建议单音频 ≤2 小时；支持任意采样率 | filetrans：大文件转写（百炼概述页提到长音频异步转写能力；单文件上限数值**未能核实**，需以最终接入的 SDK 文档「音频规格」章节为准） | 同上 |
| 个人开通流程 | 注册阿里云账号（支持支付宝/淘宝快捷登录）→ 个人实名认证（实时生效，不实名不发免费额度）→ 百炼控制台同意协议开通（**开通不产生费用、无月费，按量付费**）→ 创建 API Key | 同 | [百炼新人免费额度说明](https://help.aliyun.com/zh/model-studio/new-free-quota)、[API Key 文档](https://help.aliyun.com/zh/model-studio/get-api-key)，查证 2026-11-20 |
| 大陆直连 | 域名 help.aliyun.com / dashscope 国内端点，大陆直连稳定（阿里云国内产品线） | 同 | 架构常识 + 官方文档为国内站，无墙记录 |

**对本工具的适配性**：免费额度最慷慨（paraformer 每月 10h，月月送），价格也最低；但「本地文件 → OSS → 公网 URL」多两步（要开 OSS、有少量存储费用和管理成本），作为备选方案可接受。qwen3-asr-flash 同步接口虽支持本地文件但 10MB/5min 上限卡死长视频，不推荐。

### 2.3 Groq / OpenAI 大陆直连可达性

| 平台 | 结论 | 依据 | 来源 |
|---|---|---|---|
| **OpenAI**（whisper-1 $0.36/h，用户已知行情，价格本次未复核） | **不可直连**。官方「支持的国家和地区」清单不含中国大陆/港澳；官方明确「从不支持地区访问可能导致账户封禁或暂停」，2024-07-09 起停止来自不支持地区的 API 调用 | 官方帮助中心两篇文档 | [支持地区清单](https://help.openai.com/zh-hans-cn/articles/5347006-openai-api-supported-countries-and-territories)、[不支持地区服务说明](https://help.openai.com/zh-hans-cn/articles/9131992-chatgpt-and-api-services-in-unsupported-countries-and-territories)，查证 2026-11-20 |
| **Groq**（whisper-turbo $0.04/h，用户已知行情，价格本次未复核） | **不可直连**。groq.com / api.groq.com 大陆网络不可达；且存在区域限制（lobe-chat Issue 报告大陆提示「所在地区不支持此模型服务」）；社区用海外反代访问且有**封号案例**（groq-proxy Issue #1） | 社区反馈 + 反代项目的存在本身即为佐证 | [groq-proxy（GitHub）](https://github.com/Ikaros-521/groq-proxy)、[lobe-chat Issue #4240](https://github.com/lobehub/lobe-chat/issues/4240)，查证 2026-11-20 |

结论：两者都需要「海外 IP + 海外账号支付」，与「hf.co 直连不通、网络可达性是硬指标」的约束冲突，且违反 OpenAI/Groq 地区条款有封号风险，**直接排除**。若未来网络条件变化，Groq whisper-turbo $0.04/h 的价格优势显著，可重新评估（本次未复核其现行价与免费档）。

## 3. B 部分：总结 LLM 明细

### 3.1 智谱 GLM（bigmodel.cn）

当前价格页主力为 **GLM-5 系列**（GLM-4.6 纯文本版**已不在价格页列出，现行价未能核实**，疑似被 4.7/5.x 替代）。单位：元/百万 tokens。

| 模型 | 上下文 | 输入 | 输出 | 缓存命中输入 | 来源 |
|---|---|---|---|---|---|
| **GLM-5.3-Flash** | 1M | **0.8** | **2.8** | 0.23 | [官方定价文档](https://docs.bigmodel.cn/cn/guide/start/pricing)，查证 2026-11-20 |
| **GLM-4.7-Flash** | 200K | **免费** | **免费** | — | 同上 |
| GLM-4-Flash-250414 | 128K | 免费 | 免费 | — | 同上 |
| GLM-4.5-Air（<32K 入 / 出 ≥0.2K 档） | 128K | 0.8 | 6 | 0.16 | 同上 |
| GLM-4.7（<32K 入 / 出 ≥0.2K 档） | 200K | 3 | 14 | 0.6 | 同上 |
| GLM-5.3（旗舰，参考） | 1M | 8 | 28 | 2 | 同上 |

- 注册/计费：手机号（含海外号）+ 短信验证码注册；用户协议要求实名认证后正式使用 API；**按 token 用量计费**，充值制（新用户常有首单立减活动）。来源：[bigmodel.cn](https://bigmodel.cn)、[费用 FAQ](https://docs.bigmodel.cn/cn/faq/fee-issues)、[上新活动页](https://docs.bigmodel.cn/cn/update/promotion)，查证 2026-11-20。
- 新用户免费额度：**注册送 2000 万 tokens 组合资源包**（多模型分配，第三方实测「通用模型额度约 200 万 tokens，其余为各模型专用」）；邀请活动：邀请双方各得 2000 万 GLM-4.5-Air tokens（约 ¥58）。来源：[官方活动页](https://docs.bigmodel.cn/cn/update/promotion) + 第三方实测，查证 2026-11-20。组合包内各模型的具体分布以官网「资源包」页为准。

### 3.2 DeepSeek 官方（platform.deepseek.com）

现行价格页列 **deepseek-flash** 与 **deepseek-v4-pro** 两个模型（旧名 deepseek-chat / deepseek-reasoner 已不出现在价格页，其现行状态**未能核实**）。单位：元/百万 tokens。

| 模型 | 上下文 | 输入（缓存未命中）空闲/高峰 | 输出 空闲/高峰 | 输入（缓存命中）空闲/高峰 | 来源 |
|---|---|---|---|---|---|
| **deepseek-flash**（V4.1-Flash-1017） | 1M | **1 / 2** | **4 / 8** | 0.02 / 0.04 | [官方定价页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)，查证 2026-11-20 |
| deepseek-v4-pro | 1M | 4.5 / 9 | 13.5 / 27 | 0.15 / 0.30 | 同上 |

- 高峰时段：**北京时间周一至周五（无法定节假日）9:00-12:00、14:00-18:00**；其余为空闲（空闲价为高峰一半）。
- 注册/计费：手机号/邮箱注册，预付费充值、按量从余额扣费；**最低充值 ¥1**（第三方信源）；费用从赠送余额优先扣除。
- 新用户赠送：**约 ¥10 余额**——官方定价页未明示，来自第三方实测（CSDN 2025-08、知乎 2025-02 两个独立信源），**标注为「第三方信源，官方页面未明示」**。来源：[CSDN 免费 API 整理](https://blog.csdn.net/a772304419/article/details/149907856)、[知乎](https://zhuanlan.zhihu.com/p/21798970121)、[注册教程（知乎）](https://zhuanlan.zhihu.com/p/2005524580572348598)，查证 2026-11-20。
- 大陆直连：国内公司，官方域名直连（无墙记录）。

### 3.3 单次成本估算（输入 10k tokens ≈ 20-30 分钟视频 transcript + 输出 3k tokens 报告）

| 平台/模型 | 计费口径 | 单次成本（元/次） | 100 次/月 | 备注 |
|---|---|---|---|---|
| **智谱 GLM-5.3-Flash** | 0.8/M 入 + 2.8/M 出 | 0.008 + 0.0084 = **¥0.0164** | **≈¥1.6** | 无时段浮动 |
| 智谱 GLM-4.7-Flash / GLM-4-Flash | 免费 | **¥0** | ¥0 | 永久免费，质量需实测 |
| 智谱 GLM-4.5-Air | 0.8 入 + 6 出（出 ≥0.2K 档） | 0.008 + 0.018 = **¥0.026** | ≈¥2.6 | |
| 智谱 GLM-4.7 | 3 入 + 14 出（出 ≥0.2K 档） | 0.03 + 0.042 = **¥0.072** | ≈¥7.2 | |
| 智谱 GLM-5.3（旗舰） | 8 入 + 28 出 | 0.08 + 0.084 = **¥0.164** | ≈¥16.4 | 追求上限质量时用 |
| **DeepSeek deepseek-flash** | 空闲 1 入 + 4 出 | **¥0.022** | ≈¥2.2 | 高峰 2/8 → **¥0.044/次**（≈¥4.4/百次） |
| DeepSeek deepseek-v4-pro | 空闲 4.5 入 + 13.5 出 | **¥0.0855** | ≈¥8.6 | 高峰 9/27 → ¥0.171/次 |

要点：

1. **第一梯队（¥0.02 上下/次）**：GLM-5.3-Flash 与 deepseek-flash（空闲档）同量级，单次成本都可忽略；差异在 DeepSeek 高峰翻倍、GLM 无浮动。
2. transcript 前 10k tokens 若命中上下文缓存（智谱 0.23/M、DeepSeek 0.02-0.04/M），成本还能再降一个量级——但本工具每次视频的 transcript 都不同，缓存命中率低，**不应指望**。
3. 若对报告质量不满意，升级路径平滑：GLM-4.7（¥0.072）或 GLM-5.3（¥0.164）仍远低于人工时间成本。

### 3.4 免费额度对「测试几十次」的覆盖度

| 平台 | 额度 | 折算测试次数（10k 入/3k 出） | 置信度 |
|---|---|---|---|
| 智谱 | 2000 万 tokens 新人组合包（多模型分配）+ Flash 档永久免费 | GLM-4.7-Flash **不限次**；组合包中通用额度（第三方实测约 200 万 tokens）在 GLM-5.3-Flash 上约可测 **18 万次**（即便只有 1/10 是通用额度也远超需求） | 官方活动页确认存在；内部分布为第三方实测 |
| DeepSeek | 新用户约 ¥10 赠送余额 | deepseek-flash 空闲档 **约 450 次** | **第三方信源，官方页面未明示**；以平台「余额」页实际显示为准 |

## 4. 风险与未核实项清单

1. **SiliconFlow 免费政策可持续性**：SenseVoice-Small 当前免费，但平台「保留调整价格的权利」；若未来转收费，其付费 ASR 参考价 ¥0.00022/秒（≈¥0.79/h）仍可接受，且百炼备选随时顶上。
2. **SiliconFlow 音频格式清单未公开**（仅示例 mp3）：接入时先用 mp3/wav 各测一条。
3. **百炼 filetrans 单文件大小上限未能核实**（概述页提到支持大文件异步转写，精确数值需查最终接入的 SDK 文档「音频规格」章节）。
4. **智谱 GLM-4.6 现行价未能核实**（价格页已不列，疑被替代）；**DeepSeek 旧模型名 deepseek-chat/reasoner 现行状态未能核实**。本报告一律以现行价格页在售型号为准。
5. **DeepSeek ¥10 新人赠送**、**最低充值 ¥1** 为第三方信源，官方页面未明示，注册后以余额页为准。
6. **Groq 免费额度**（dev tier）本次未核实——因大陆不可直连已整体排除，不影响结论。
7. 所有价格为 2026-11-20 快照，接入前建议按文中官方链接复核一遍价格页。

## 5. 信息来源汇总（均为 2026-11-20 查证）

**硅基流动**：[价格页](https://siliconflow.cn/pricing) · [音频转写 API 文档（50MB/1h）](https://api-docs.siliconflow.cn/docs/api/audio-transcriptions-post) · [速率限制](https://docs.siliconflow.com/cn/userguide/rate-limits/rate-limit-and-upgradation) · [实名认证 FAQ](https://api-docs.siliconflow.cn/docs/userguide/faqs/authentication) · [SenseVoice 上线公告（知乎）](https://zhuanlan.zhihu.com/p/714291231)

**阿里云百炼**：[模型价格页](https://help.aliyun.com/zh/model-studio/model-pricing) · [paraformer 非实时识别 Python SDK](https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-python-sdk) · [qwen3-asr-flash-filetrans 模型页](https://help.aliyun.com/zh/model-studio/qwen3-asr-flash-filetrans) · [Qwen ASR API 参考（10MB/5min）](https://www.alibabacloud.com/help/zh/model-studio/qwen-asr-api-reference) · [新人免费额度](https://help.aliyun.com/zh/model-studio/new-free-quota) · [API Key](https://help.aliyun.com/zh/model-studio/get-api-key)

**Groq / OpenAI 可达性**：[OpenAI 支持地区清单](https://help.openai.com/zh-hans-cn/articles/5347006-openai-api-supported-countries-and-territories) · [OpenAI 不支持地区说明](https://help.openai.com/zh-hans-cn/articles/9131992-chatgpt-and-api-services-in-unsupported-countries-and-territories) · [groq-proxy（GitHub）](https://github.com/Ikaros-521/groq-proxy) · [lobe-chat Issue #4240](https://github.com/lobehub/lobe-chat/issues/4240)

**智谱**：[官方定价文档](https://docs.bigmodel.cn/cn/guide/start/pricing) · [上新/邀请活动页](https://docs.bigmodel.cn/cn/update/promotion) · [费用 FAQ](https://docs.bigmodel.cn/cn/faq/fee-issues) · [bigmodel.cn](https://bigmodel.cn)

**DeepSeek**：[官方定价页（中文）](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) · [开放平台](https://platform.deepseek.com) · [免费额度第三方整理（CSDN）](https://blog.csdn.net/a772304419/article/details/149907856) · [新人额度（知乎）](https://zhuanlan.zhihu.com/p/21798970121) · [注册教程（知乎）](https://zhuanlan.zhihu.com/p/2005524580572348598)

## 6. 实测补录（2026-11-20）

> 桌面调研同日用真实 API 补做三项轻量实测（curl 直连，无代理；密钥经环境变量注入，未落盘）。合计花费 **¥0**（SenseVoice 为免费模型；GLM 消耗 92 tokens、DeepSeek 9 tokens，忽略不计）。本节全部为实测值，对照修正上文桌面调研结论。

### 6.1 硅基流动 SenseVoice-Small 云转写

实测素材：`runs/asr-bench/` 的 B站演讲视频音频——切片 `slice_600_300.wav`（300s / 9.2MB / 16kHz mono wav）与全片 `audio.m4a`（55min / 127MB）；本地参照为 sherpa-onnx SenseVoice 输出（`out/sensevoice.txt`，本地耗时 18.8s/300s，见 `01-本地ASR引擎对比.md`）。

| 项目 | 实测结果（2026-11-20） |
|---|---|
| **端点与鉴权（关键发现）** | **`.com` 与 `.cn` 两站密钥不互通**。按 §2.1 原文调 `api.siliconflow.com` 返回 `401 {"code":30014,"message":"Token is invalid."}`（完整上传 9.6MB 后被拒，31.2s）；**换 `https://api.siliconflow.cn` 同一密钥立即 200**。本账号密钥来自中国站控制台 cloud.siliconflow.cn，**本项目应固定用 `.cn` 端点** |
| 模型名 | `FunAudioLLM/SenseVoiceSmall` 原样正确，一次通过，无需修正 |
| 300s 切片转写 | **HTTP 200，wall 8.85s**（含 9.6MB 上传）。响应 `{text, language:"Chinese", usage:{type:"duration", seconds:300}}`——计费口径按音频时长（秒），免费模型 ¥0 |
| **转写质量（CER）** | 云端 vs 本地同模型输出 **CER 1.43%**（编辑距离 16 / 参考 1121 字，`cer.py` 归一化口径）。差异来源：云端保留 SenseVoice 原生情绪/音乐标记（😊 🎼，本地管线已剥离），另有零星标点与语气词切分差（如「认识了20年(，)一直」）。**同一模型云端本地输出实质等价** |
| **55min 全片（127MB m4a）** | **HTTP 200，wall 64.6s**（上传 132.9MB），usage 3332s，输出 12,442 字。**文档「单文件 ≤50MB」实测未拦截**——127MB m4a 一次直传成功；m4a 格式实测可用 |
| 语种字段 | `language` 回显不可靠：全片报 "English"（片头英文 BGM 所致），切片为 "Chinese"，不能当语种判断依据 |
| 延迟量级 | 300s 音频：本地 18.8s vs 云端 8.85s（含上传，≈34 倍速）；3300s 音频云端单调用 64.6s（≈52 倍速） |

**对 §2.1 的修正与补充**：

1. **端点**：国内账号实际可用端点为 `https://api.siliconflow.cn/v1/audio/transcriptions`（`.com` 为国际站、密钥隔离）。大陆直连确认无障碍。
2. **文件上限**：「≤50MB」为文档标称值，实测 127MB 未被拦截；但不建议依赖该行为（平台可随时收紧），超长视频仍按原方案预留切片兜底。1h 时长上限本次 55min 在限内，超限行为未测。
3. **音频格式**（§4.2 风险项部分解决）：wav、m4a 均实测可用，无需先转 mp3。
4. **结论增强**：降级路线（老 Intel Mac 备胎）成立——一条 55min 视频一次调用 64.6s 拿到 transcript，零费用，且无需预压缩/预切片（当前政策下）。

### 6.2 智谱 GLM 连通性

| 项目 | 实测结果（2026-11-20） |
|---|---|
| `glm-5.3-flash` | **HTTP 200，一次通过**（未动用 glm-4-flash 回退）。延迟 2.14s，正确回复「正常」。usage：入 16 / 出 76 / 合计 92 tokens |
| 注意点 | **GLM-5.3-Flash 默认带思考**：「回复两个字」这种零思考指令也产生 73 个 reasoning tokens（`reasoning_content` 字段，计入输出计费）。对本项目影响可忽略（总结任务本就需要推理），但极简调用也有固定输出开销，做延迟/成本预估时别按「输出=正文」估 |

### 6.3 DeepSeek 连通性

| 项目 | 实测结果（2026-11-20） |
|---|---|
| `deepseek-chat` | **HTTP 200，0.76s**，正确回复「正常」。usage：入 8 / 出 1 / 合计 9 tokens |
| 旧模型名状态（补 §4.4 未核实项） | 请求 `deepseek-chat` 被正常接受，响应 `model` 字段回显 **`deepseek-flash`**——旧名是**别名，底层已指向现行 deepseek-flash**，存量代码无需改模型名 |

### 6.4 修正汇总（桌面调研 → 实测）

| 原章节 | 原结论（桌面调研） | 实测修正（2026-11-20） |
|---|---|---|
| §2.1 接口 | `api.siliconflow.com` | 国内密钥须用 `api.siliconflow.cn`；`.com` 对中国站密钥 401（站点隔离） |
| §2.1 文件上限 | ≤50MB、≤1h | **50MB 实测未拦截**（127MB m4a 200 通过）；1h 未测超限 |
| §2.1 / §4.2 音频格式 | 仅示例 mp3，清单未公开 | wav、m4a 实测可用 |
| §3.1 GLM-5.3-Flash | 价格页在售 | 实测可用、延迟 2.1s；注意 Flash 档也带 reasoning tokens |
| §4.4 deepseek-chat 状态 | 未能核实 | 确认为 deepseek-flash 的**别名**，可正常调用（响应回显新名） |

来源：本节为 2026-11-20 实测（curl 直连 + `tools/asr-bench/cer.py`，素材见 `runs/asr-bench/`）。命令中密钥以 `$SILICONFLOW_API_KEY` / `$ZHIPU_API_KEY` / `$DEEPSEEK_API_KEY` 环境变量注入（`~/.config/ai-tools/bili-digest/keys.env`），值未落盘。
