"""时间轴融合：transcript 段 + 帧描述按时间戳归并（S6 交接文档 §3）。

产物两用：
- enriched_transcript.md（run 目录）：🗣 语音/字幕 与 🖼 画面交错呈现，
  帧以相对路径 frames/xx.jpg 引用（与 report.md 的嵌帧路径一致）；
- summarize prompt 注入行：交给报告模型按帧 id 挑选嵌入 report.md 的帧。
frames 入参是 meta["frames"] 形态的 dict 列表（id/t/file/desc/has_text），
不依赖 transcribe 数据结构（load_run 补报告时也能直接用 meta.json 里的帧）。
"""


def clock(sec) -> str:
    return "%02d:%02d" % (int(sec) // 60, int(sec) % 60)


def _segments_of(segments):
    """兼容 Transcript 对象与 Segment 列表两种入参。"""
    return getattr(segments, "segments", segments)


def build_timeline(segments, frames) -> list[tuple[float, str, object]]:
    """段（有文本）与帧按时间戳升序归并；同刻帧排在语音前（先看到画面再看解说）。"""
    entries = [(float(f.get("t") or 0), "frame", f) for f in frames]
    entries += [(s.start, "speech", s) for s in _segments_of(segments) if s.text.strip()]
    entries.sort(key=lambda e: e[0])
    return entries


def render_enriched_md(transcript, frames, meta) -> str:
    """富化时间轴 Markdown（enriched_transcript.md 内容）；transcript 可为 Transcript 或段列表。"""
    title = (meta or {}).get("title") or (meta or {}).get("bvid") or (meta or {}).get("note_id")
    note = (f"> ⚠️ 画面价值门控命中（{meta['visual_gate']}）：画面以人物口播为主，未提取信息性画面\n"
            if (meta or {}).get("visual_gate") else "")
    head = [
        f"# {title} · 富化时间轴",
        "",
        note,
        f"> 🗣 语音/字幕 · 🖼 画面识别 · 共 {len(frames)} 帧（图片在 frames/ 目录）",
        "",
    ]
    body = []
    for t, kind, item in build_timeline(transcript, frames):
        if kind == "speech":
            body.append(f"[{clock(t)}] 🗣 {item.text.strip()}")
        else:
            ref = f" ![{item.get('id', '')}]({item.get('file', '')})"
            body.append(f"[{clock(t)}] 🖼 （画面）{(item.get('desc') or '').strip()}{ref}")
    return "\n".join(head + body)


def frame_lines_for_prompt(frames) -> list[str]:
    """帧描述 → prompt 注入行：`[mm:ss] f00（画面，含文字）desc`。"""
    lines = []
    for f in frames:
        mark = "画面，含文字" if f.get("has_text") else "画面"
        lines.append(f"[{clock(f.get('t') or 0)}] {f.get('id')}（{mark}）{f.get('desc', '')}")
    return lines
