# 本地 ASR 引擎对比（无字幕视频回退方案选型）

> Phase 1 调研 · 2026-11-20 · 结论见文末「选型建议」

## 1. 测试条件

- **机器**：Apple M4 Pro / 64GB RAM / macOS（arm64）。**Intel 侧无实机，兼容性为架构层面判断，速度未实测**，报告中已标注。
- **音频**：`runs/asr-bench/audio/slice_600_300.wav`，300 秒 / 16kHz / 单声道 / s16，中文单人演讲（B站12周年演讲 BV1CV411s7jd P1 的 10:00–15:00，陈睿讲话，语速正常、吐字清晰）。
- **计时口径**：`/usr/bin/time -lp` 的 **real（wall time，含模型加载与进程启动）** 与 **maximum resident set size（peak RSS）**。各引擎脚本另在 stderr 自报「纯转写耗时」（不含模型加载），表中单独列出备查。四个引擎口径一致：whisper.cpp 沿用先前实测；mlx / faster / sensevoice 为本次重跑取数（模型已缓存，无下载时间混入）。
- **交叉 CER 口径**：`scripts/asr-bench/cer.py`（NFKC、去标点空白、小写后的字符级编辑距离）。
- **模型下载**：hf.co 直连不通，HF 模型一律经 `HF_ENDPOINT=https://hf-mirror.com`；SenseVoice 经 GitHub release 直连。

**Caveat（重要）**：
1. **无真值参考**——B站字幕接口当日被风控污染（返回无关视频的字幕），因此 CER 以 whisper.cpp turbo 输出为**锚点**，只衡量引擎间一致性，**不代表绝对识别错误率**。
2. **单语料、单切片**：仅 5 分钟清晰单人中文演讲；未覆盖多人、嘈杂、背景音乐、中英混说等场景，结论外推需谨慎。
3. SenseVoice 压缩包交接时记录 368MB 系下载中途误判，实际 `sensevoice.tar.bz2` 为 999MB（1,047,870,769 B），本次测试开始时后台 curl 才下载完成。

## 2. 结果总表

| 引擎/模型 | wall time | ×实时（300s 音频） | peak RSS | 模型体积 | 安装方式 | Intel Mac 兼容性 |
|---|---|---|---|---|---|---|
| whisper.cpp 1.9.5 + large-v3-turbo (ggml) | **14.41 s** | 20.8× | **2.08 GB** | 1.5 GB (`ggml-large-v3-turbo.bin`) | brew（6.3MB 二进制） | ✅（CPU；Intel 无 Metal） |
| mlx-whisper 0.4.3 + large-v3-turbo (MLX fp16) | **12.42 s** | 24.2× | **1.89 GB** | 1.5 GB (`weights.safetensors`) | pip（.venv） | ❌ 仅 Apple Silicon |
| faster-whisper 1.2.1 + large-v3-turbo int8（CPU） | **79.96 s** | 3.75× | **2.40 GB** | 1.5 GB（CT2 `model.bin`，fp16 权重 int8 量化加载） | pip（.venv） | ✅（CPU） |
| sherpa-onnx 2.0.2 + SenseVoice int8（CPU） | **19.51 s** | 15.4× | **2.93 GB** | **228 MB**（`model.int8.onnx`；包内另有 fp32 894MB 未用） | pip（.venv）+ GitHub 下载模型 | ✅（CPU，官方 x86_64 轮子） |

- peak RSS 换算：whisper.cpp 2,083,946,496 B / mlx 1,887,076,352 B / faster 2,398,502,912 B / sensevoice 2,930,458,624 B（GB = 10⁹ 口径）。
- 脚本自报纯转写耗时（不含模型加载，仅参考）：mlx 10.8s；faster 77.0s（含 VAD）；sensevoice 18.8s（仅 decode）。whisper.cpp 无此口径。
- 计时原始记录：`runs/asr-bench/out/{whisper_cpp_turbo,mlx_turbo,faster_turbo,sensevoice}.time`。
- faster-whisper 运行时 ctranslate2 提示「experimental MPS backend 可用、常快 2 倍+」，本次按 CPU 口径未开启（兼顾 Intel 可复现）。

## 3. 交叉 CER（锚点 = whisper.cpp turbo 输出）

| 待测 | CER（对锚点） | 编辑距离 | 备注 |
|---|---|---|---|
| faster-whisper turbo int8 | **0.358%** | 4 / 1116 字 | 同源模型（CT2 版 turbo），几乎逐字一致 |
| mlx-whisper turbo | **1.165%** | 13 / 1116 字 | 差异主要是语气词（“啊”）与个别专有名词 |
| SenseVoice int8 | **1.882%** | 21 / 1116 字 | 异构模型，差异含语气词、数字 ITN 格式、标点（归一化已去标点） |

> 再强调：锚点不是真值，CER 只说明「四个引擎输出高度一致、无一方系统性跑偏」。1–2% 量级的差异主要来自语气词增删、数字格式（“70%” vs “百70”）和专有名词用字，而非大面积识别错误。

## 4. 开头 300 字对照（原文引用）

**whisper.cpp turbo**（分行无标点，此处换行并为空格）：

> 有一次我和我的一个大学同学吃饭 他专门跟我说 他在B站上面看了这个风险少年的天空 这个让我很开心 为什么呢 因为我跟他认识了20年 一直想尝试他使用B站 都未果 最后是我们的这个自制剧集 把它转化成了B站的用户 刚才我谈到了我们的up主 我们的ACG类的内容 我们的自制内容 我相信很多用户都能够感觉到 B站是非常重视内容品质的 确实坚持品质优先 是B站过去十几年来一直在做的事情 我们除了对于内容品质优先的坚持之外 我们还坚持创作者优先 坚持正向的内容价值观 接下来的话 我也会讲一讲B站的这几个坚持 好的内容一定是源于好的创作者 所以服务创作者 服务up主 一直是B站最重要的工作 没有之一 在我们现在9500名员工里面 有2408人是全职的为up主服务的 这个数量和去年相比 同比提升了42% 同时除了对up主做服务工作以外 我们也一直坚持把大部分的流量分配给中小的up主 我们现在有70%的流量是分配给中小的up主和新的up主的

**mlx-whisper turbo**（连排无标点）：

> 有一次啊我和我的一个大学同学吃饭他专门跟我说他在B站上面看了这个风犬少年的天空这个让我很开心为什么呢因为我跟他认识了20年一直想尝试他使用B站都未果最后是我们的这个自制剧集把它转化成了B站的用户刚才我谈到了我们的up主我们的ACG类的内容我们的自制内容我相信很多用户都能够感觉到B站是非常重视内容品质的确实坚持品质优先是B站过去十几年来一直在做的事情我们除了对于内容品质优先的坚持之外我们还坚持创作者优先坚持正向的内容价值观接下来的话我也会讲一讲B站的这几个坚持好的内容一定是源于好的创作者所以服务创作者服务up主一直是B站最重要的工作没有之一在我们现在9500名员工里面有2408人是全职的为up主服务的这个数量和去年相比同比提升了42%同时除了对up主做服务工作以外我们也一直坚持把大部分的流量分配给中小的up主我们现在有70%的流量是分配给中小的up主和新的up主的

**faster-whisper turbo int8**（连排无标点）：

> 有一次我和我的一个大学同学吃饭他专门跟我说他在B站上面看了这个风险少年的天空这个让我很开心为什么呢因为我跟他认识了20年一直想尝试他使用B站都未果最后是我们的这个自制聚集把它转化成了B站的用户刚才我谈到了我们的up主我们的ACG类的内容我们的自制内容我相信很多用户都能够感觉到B站是非常重视内容品质的确实坚持品质优先是B站过去十几年来一直在做的事情我们除了对于内容品质优先的坚持之外我们还坚持创作者优先坚持正向的内容价值观接下来的话我也会讲一讲B站的这几个坚持好的内容一定是源于好的创作者所以服务创作者服务up主一直是B站最重要的工作没有之一在我们现在9500名员工里面有2408人是全职的为up主服务的这个数量和去年相比同比提升了42%同时除了对up主做服务工作以外我们也一直坚持把大部分的流量分配给中小的up主我们现在有70%的流量是分配给中小的up主和新的up主的

**SenseVoice int8**（自带标点）：

> 有一次啊我和我的一个大学同学吃饭，他专门跟我说，他在B站上面看了这个风险少年的天空。哎，这个让我很开心，为什么呢？因为我跟他认识了20年一直想尝试他使用B站都未果。最后是我们的这个自制聚集，把它转化成了B站的用户。刚才我谈到了我们的up主我们的ACG类的内容，我们的自制内容。我相信很多用户都能够感觉到B站是非常重视内容品质的。确实坚持品质优先，是B站过去十几年来一直在做的事情。我们除了对于内容品质优先的坚持之外，我们还坚持创作者优先，坚持正向的内容价值观。接下来的话，我也会讲一讲B站的这几个坚持。好的内容一定是源于好的创作者。所以服务创作者服务up主一直是B站最重要的工作，没有之一。在我们现在9500名员工里面，有2408人是全职的为up主服务的。这个数量和去年相比同比提升了42%。同时除了对up主做服务工作以外，我们也一直坚持把大部分的流量分配给中小的up主。我们现在有百70的流量，是分配给中小的up主和新的up主的。

### 人工抽检要点（全文通读 + 横向对比）

| 观察点（真值） | whisper.cpp | mlx | faster | SenseVoice |
|---|---|---|---|---|
| 「风犬少年的天空」（B站自制剧名） | ❌ 风险少年 | ✅ **风犬少年** | ❌ 风险少年 | ❌ 风险少年 |
| 「必剪」（B站剪辑工具） | ❌ B减 | ❌ B减 | ❌ B减 | ❌ B检 |
| 「入站考试」 | ❌ 入战考试 | ❌ 入战考试 | ❌ 入战考试 | ✅ **入站考试** |
| 「自制剧集」 | ✅ | ✅ | ❌ 自制聚集 | ❌ 自制聚集 |
| 「新人up主」 | ❌ 新任 | ✅ 新人 | ✅ 新人 | ✅ 新人 |
| 标点 | 无（仅分行） | 无 | 无 | ✅ 自带逗号句号问号 |
| 语气词（啊/哎） | 丢弃 | 保留 | 丢弃 | 保留 |
| 数字 ITN | 正常（70%、1.1个亿） | 正常 | 正常 | 有毛刺：「百70的流量」「一0百道」「一点一个亿」 |
| 结尾行为 | 「我们B站的」 | 「我们B站B站B站」（尾部重复幻觉） | 「我们B站B站」（尾部重复） | 「我们B站。」干净收尾 |

总体：四个引擎在清晰单人中文演讲上**内容完整度与连贯性都达标**，差异集中在专有名词与格式层。whisper 系三家（同 large-v3-turbo 权重）错法几乎相同；SenseVoice 在「入站考试」这类语境词上更准、标点是独有能力，但数字 ITN 毛刺肉眼可见（「百70」）。turbo 系模型在音频末尾都有轻微重复幻觉，mlx 最明显——对“文本喂 LLM 做总结”的场景影响很小，但值得知道。

## 5. 分引擎小结

**whisper.cpp + large-v3-turbo（brew，ggml）**
- 质量：锚点水平，连贯流畅，无标点仅分行；「风犬→风险」「必剪→B减」等同音字错误典型。
- 速度/资源：14.4s（20.8×）很理想，但 peak RSS 2.08GB 且模型 1.5GB，对老 Intel 机器（常见 8–16GB 内存）偏重；Intel 上无 Metal 纯 CPU，速度未实测、预计明显慢于 M4。
- 工程化：brew 一条命令装好 `whisper-cli`，CLI 参数成熟（VAD、分段、语言），双平台可用。模型下载需 hf-mirror。

**mlx-whisper + large-v3-turbo（pip，MLX）**
- 质量：与 whisper.cpp 同档，个别专有名词反而更好（风犬少年）；无标点；尾部重复幻觉稍明显。
- 速度/资源：**全场最快 12.4s（24.2×）、RSS 最低 1.89GB**。
- 工程化：pip 安装简单，Python API 一行转写；**致命约束：MLX 仅 Apple Silicon，Intel 直接出局**（选型硬约束）。

**faster-whisper + large-v3-turbo int8（pip，CPU）**
- 质量：与 whisper.cpp 几乎逐字一致（CER 0.36%）。
- 速度/资源：**最慢，79.96s 仅 3.75× 实时**（M4 的 CPU 上尚且如此，老 Intel 上恐逼近甚至低于实时）；RSS 2.40GB。VAD 是它的加分项（自动跳静音，长音频友好）。
- 工程化：pip 安装、API 干净、跨平台；ctranslate2 有实验性 MPS 后端（本次未开，Intel 无关）。在本项目约束下不占优。

**SenseVoice int8（pip sherpa-onnx，CPU）**
- 质量：内容完整连贯，**唯一自带标点**，「入站考试」全场唯一正确；短板是数字 ITN 毛刺（「百70」「一0百道」）和专有名词无优势（风险少年、B检）。
- 速度/资源：19.5s（15.4×）纯 CPU 即可达成，**模型仅 228MB**（体积优势数量级领先）；RSS 2.93GB 偏高（ONNX Runtime 加载 + 300s 音频一次性整段 decode），但对 64GB/16GB 机器均可接受。
- 工程化：pip 装 sherpa-onnx（官方多平台轮子，x86_64 支持明确），模型 GitHub 直连可下；注意 sherpa-onnx 2.0.2 Python API 无 `read_wave`，需自读 wav（`scripts/asr-bench/run_sense.py` 已修，用标准库 wave + numpy）。原生中英日韩粤五语，贴合 B站场景，但非通用多语种。

## 6. 选型建议

- **Apple Silicon 机器：mlx-whisper + large-v3-turbo**。24×实时、RSS 最低、质量同档最优（专有名词表现还更好）；MLX 限制在 AS 上不是问题。
- **Intel 机器：SenseVoice（sherpa-onnx int8）**。纯 CPU 15×实时（M4 实测），x86_64 官方支持，模型仅 228MB（下载/存储友好），自带标点；数字 ITN 毛刺对「喂 LLM 做总结」的下游影响可控。faster-whisper 在 M4 CPU 上只有 3.75×，老 Intel 上预计不可用，**不推荐**；whisper.cpp 双平台可用但 2GB 级 RSS + 1.5GB 模型对老机器偏重，留作备选。
- **统一引擎方案：可行，选 SenseVoice**。它在 AS 上 CPU 就有 15×（不依赖 MLX 也能跑），双平台同一套代码同一套模型（228MB），运维最简；代价是放弃 turbo 系在专有名词上的部分优势。若追求各平台性能最优且能维护两套后端，则用 **mlx（AS）+ SenseVoice（Intel）双引擎**，在转写接口层做一层抽象即可（两者输出都是纯文本，接口差异极小）。

**一句话结论：AS 用 mlx-whisper，Intel 用 SenseVoice，两引擎经一层转写接口抽象并存；若坚持单一引擎统一两端，选 SenseVoice（sherpa-onnx int8）。**

## 7. 复现

```bash
# 环境：scripts/asr-bench/.venv（faster-whisper 1.2.1 / mlx-whisper 0.4.3 / sherpa-onnx 2.0.2）
cd scripts/asr-bench
# whisper.cpp（brew whisper-cli）
/usr/bin/time -lp whisper-cli -m ../../runs/asr-bench/models/ggml-large-v3-turbo.bin \
  -l zh -f ../../runs/asr-bench/audio/slice_600_300.wav -otxt -of ../../runs/asr-bench/out/whisper_cpp_turbo
# mlx / faster / sensevoice
HF_ENDPOINT=https://hf-mirror.com /usr/bin/time -lp .venv/bin/python run_mlx.py \
  ../../runs/asr-bench/audio/slice_600_300.wav ../../runs/asr-bench/out/mlx_turbo.txt
HF_ENDPOINT=https://hf-mirror.com /usr/bin/time -lp .venv/bin/python run_faster.py \
  ../../runs/asr-bench/audio/slice_600_300.wav ../../runs/asr-bench/out/faster_turbo.txt large-v3-turbo int8
/usr/bin/time -lp .venv/bin/python run_sense.py \
  ../../runs/asr-bench/audio/slice_600_300.wav \
  ../../runs/asr-bench/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17 \
  ../../runs/asr-bench/out/sensevoice.txt
# CER（锚点 whisper.cpp）
.venv/bin/python cer.py ../../runs/asr-bench/out/whisper_cpp_turbo.txt ../../runs/asr-bench/out/mlx_turbo.txt
```

## 8. 后记（2026-11-20 补）

bilibili-transcriber 实测（见 [03-bili-transcriber实测.md](03-bili-transcriber实测.md)）发现：sherpa-onnx SenseVoice 以 `num_threads=min(8, …)` 多线程解码时，311 秒音频转写仅 2–3 秒（**约 100–150× 实时**），比本报告 §2 的默认单线程口径（19.5s / 15.4×）**快约一个数量级**。本报告的 SenseVoice 数字应视为保守下限；多线程是后续管线的零成本提速点（Intel 机器同理受益）。「统一引擎 = SenseVoice」方案的性价比因此进一步提升，见进度记录 Phase 1。
