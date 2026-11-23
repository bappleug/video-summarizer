# S1 交接文档 — bili-digest B站 transcript 管线闭环

> Phase 3 / S1 · 依据 [方案设计 v1.1](../方案设计.md)。本文档自包含，实施者按此执行。

## 目标

新建 `tools/bili-digest/` Python 包，实现 B站视频「URL → transcript」完整闭环：**字幕优先（含污染校验降级）→ 无字幕回退本地 mlx ASR**，CLI 可用、测试全绿。

## 范围（S1 只做这些）

**做**：config / platforms/bilibili / audio / asr(base+mlx) / validate / pipeline / cli + pyproject + README + tests。
**不做**：summarize、cloud_sf、MCP、小红书、SQLite（S2-S5）。接口位置按方案设计留好即可，不写占位空壳。

## 复用资产（复制+署名，不 import 跨目录）

| 来源 | 用途 | 改造 |
|---|---|---|
| `tools/bili-subtitle/bili.py`（672 行，stdlib） | 登录态/续期/WBI/字幕抓取 → `platforms/bilibili.py` | 改造为模块函数（供 pipeline 调用），保留 CLI 兼容入口不做；凭据路径不变 `~/.config/ai-tools/bili/credentials.json`；文件头注明「复制自 tools/bili-subtitle/bili.py」 |
| `tools/asr-bench/fetch_audio.py` | DASH 音频下载 → `audio.py` | 增加：音轨选择、m4a 临时文件清理、ffmpeg 转码校验；署名同上 |
| `runs/asr-bench/run_mlx.py` | mlx 转写 → `asr/mlx_backend.py` | 对齐 Transcriber 协议；模型 `mlx-community/whisper-large-v3-turbo` |

## 冻结接口（方案设计 §2，不得擅自变更）

```python
# asr/base.py
@dataclass
class Segment: start: float; end: float; text: str
@dataclass
class Transcript:
    segments: list[Segment]; lang: str
    engine: str; source: str; elapsed_s: float

class Transcriber(Protocol):
    name: str
    def available() -> bool
    def transcribe(wav: Path, lang="zh") -> Transcript
```

CLI（内容 stdout / 进度 stderr / 退出码 0 成功 2 用法 3 未登录或无字幕且 ASR 失败 4 网络风控）：
```
digest <url|BV号> [--page N] [--format txt|srt|md|json] [--engine auto|mlx] [--lang zh]
digest login / digest status
```
auto 引擎选择：arm64 且 mlx 可导入 → mlx；否则 S1 阶段直接报「S2 将支持 cloud，当前设备需 AS + mlx」。

## validate 三规则（污染字幕防御，core 逻辑）

1. 时长覆盖：字幕段总时长 ≥ 视频时长 60%
2. 语言一致：中文字符占比 ≥ 50%（zh 视频假设）
3. 重复率：连续完全相同的行占比 < 30%
任一不过 → stderr warning（注明触发规则）→ 走 ASR。**降级不报错。**

## 环境事实

- Apple M4 Pro / macOS；`/opt/homebrew/bin/python3.11`；`uv` 0.13.9 已装（建议 `uv venv` + `uv pip install -e ".[mlx]"` 开发）。
- mlx-whisper 模型已缓存（HF cache，勿重新下载；config.py 里 `HF_ENDPOINT` 默认 `https://hf-mirror.com` setdefault）。
- B站登录凭据有效（28 天+），**只读复用，不打印内容**。
- 测试视频：BV1CV411s7jd——P1 有字幕（字幕路径），P5 无字幕（ASR 路径，311s）。手测产物放 `runs/s1-test/`，单测用 tmp 目录不落 runs/。
- ffmpeg 已装。

## 验收标准（全部满足）

1. `digest BV1CV411s7jd --page 1` 走字幕，txt 输出与 `bili fetch --page 1` 内容一致性 >95%（允许格式差异）。
2. `digest 'https://www.bilibili.com/video/BV1CV411s7jd?p=5'` 走 mlx ASR，300 秒级音频转写在 1 分钟内出稿，srt/json 格式可解析。
3. 构造污染字幕样本（单测内造假数据：乱码/重复行/过短覆盖），validate 三规则各自触发并正确降级。
4. `pytest` 全绿；README 含安装/用法/退出码。
5. 代码纪律：stdlib 优先（core 零第三方依赖，mlx 相关 import 放 backend 内延迟导入）；文件头署名复制来源。

## 边界（硬性）

- 只新建/修改 `tools/bili-digest/**`；不碰其他 tools/、vendors/、docs/、AGENTS.md。
- **不执行任何 git 命令**（主 agent 验收后统一提交）。
- venv 装 `.venv`（gitignore 已覆盖），装完报台账（包数/大小）。
- 遇到 B站风控（412/污染字幕真实样本）如实记录，不要反复重试。
