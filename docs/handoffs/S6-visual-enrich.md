# S6 交接文档 — 视觉增强 enrich 组件

> Phase 3 / S6 · 依据 [方案设计 v1.2](../方案设计.md) §6 S6 行与 PRD P2。前置：小修（无语音检测/目录锚定）已合入。本文档自包含。

## 目标

`digest <url> --visual`：提取视频关键帧 → VLM 读图 → 与 transcript 融合成富化时间轴 → 报告引用/嵌入关键帧。让「画面信息量 ≥ 语音」的视频（讲义/图文卡笔记）也能出有内容的报告。

**验收的杀手锏案例**：零语音笔记 6aa6179a000000002603143d（纯 BGM + 画面文字，whisper 只产幻觉）——S6 后 `--visual --summarize` 的报告必须包含画面中的真实知识点（经纬网/五带相关内容），不再出现「内容无法获取」。

## 架构（transcribe 与 summarize 之间的可选组件）

```
pipeline (--visual)
  → 下载视频流（B站 playurl DASH video / 小红书 mp4 直链，mp4 落 run 目录 download/）
  → enrich/frames.py：ffmpeg 场景检测取帧（本地确定性）
  → enrich/vlm.py：智谱 GLM-4V 批量读图（带笔记标题上下文，逐帧出 desc + 时间戳）
  → enrich/fusion.py：transcript 段 + 帧按时间戳合成富化时间轴
      → enriched_transcript.md（交错呈现，帧以相对路径 frames/xx.jpg 引用）
  → summarize（prompt 注入帧描述列表；报告模型挑帧 id 嵌入 report.md）
```

## 工作项细节

### 1. 取帧（enrich/frames.py，本地零成本）
- 场景检测：`ffmpeg -i <video> -vf "select='gt(scene,0.3)',showinfo" -f null -` 解析 pts_time 切点；每个切点后取 1 帧（`ffmpeg -ss <t> -i <video> -frames:v 1`）。
- 约束：**≤20 帧、最小间隔 5s**；无切点（长静止画面）→ 按时长均匀采样兜底（cap 内）。
- 去重（可选加分）：numpy 已在 venv，8x8 DCT pHash，与已保留帧汉明距离 <10 的丢弃；做不动就靠最小间隔，如实记录。
- 帧规格：缩放到长边 ~672px JPEG q80（控制 VLM token），存 `<run>/frames/`。
- **单测用 ffmpeg 合成视频**（testsrc + 定时切换颜色/图案），全程无网络。

### 2. VLM 读图（enrich/vlm.py，智谱，复用 ZHIPU_API_KEY）
- **模型名与价格先查证**（open.bigmodel.cn 当前文档，写死常量+查证日期注释）：预期 glm-4v 系列，有免费/低价档优先；OpenAI 兼容 content 数组 + base64 image_url。
- 批量逐帧：prompt 带视频标题/作者，「描述这一帧画面中的文字内容与图表信息，尤其转录画面上可见的文字」，返回结构化 `{t, desc, has_text}`。
- usage/成本记 meta.json（`vision` 字段）；错误分类指引复用 doctor 体系文案风格。

### 3. 融合与报告
- fusion：按时间戳归并 segments 与 frames → enriched_transcript.md（格式如 `[03:12] 🗣 …` / `[03:40] 🖼 （画面）…`）。
- summarize 模板升级：prompt 注入帧描述列表；指示模型挑选 3-8 个「信息价值最高的帧」以 id 引用；report.md 用 `![03:40](frames/f07.jpg)` 嵌入（md 相对路径）。
- **suspected_silent 协同**（小修产出）：无语音标记 + --visual 时，帧描述成为报告主内容源，报告开头注明「本视频无语音，内容来自画面识别」。

### 4. CLI / MCP
- `digest <url> --visual [--summarize]`；B站与小红书都支持。
- MCP `digest_video` 加可选 `visual` 参数（schema 更新 + 测试）。
- video 下载与 audio 下载分开（audio.py 保持不动，新逻辑放 enrich/ 或复用其下载函数，署名纪律同前）。

## 验收标准（逐条证据）

1. 合成视频单测：场景切点被检出、帧数 cap/最小间隔生效、无切点走均匀采样。
2. **杀手锏 E2E**：6aa6179a（零语音）`--visual --summarize` → report.md 含画面真实知识点 + 嵌入帧图片（frames/ 下文件存在）。
3. 对照 E2E：6aeaf852（地理课，有字幕）`--visual --summarize` → 报告含画面帧引用且字幕内容不受影响（回归）。
4. suspected_silent + --visual 提示链路单测。
5. pytest 全绿；VLM 总花费 <¥0.5（含开发迭代）；README 更新（--visual 用法/费用说明/已知限制）。
6. B站 `--visual` 至少跑通一条（P5 或任一短视频）或如实记录阻断。

## 边界（硬性）

- 只动 `tools/bili-digest/**`；不碰 docs/vendors/其他 tools；**不执行 git**。
- 零新增 pip 依赖（numpy 已有、ffmpeg 系统、VLM 用 stdlib urllib）。
- 真实请求预算：小红书 ≤4（两条笔记各一次页面+视频）、B站 ≤2；间隔 ≥30s；被拦如实记录。
- key 不打印；完成报告：增量文件/行数、验收逐条证据、VLM 花费、查证到的 VLM 模型名与价格、坑。
