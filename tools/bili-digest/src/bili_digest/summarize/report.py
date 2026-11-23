"""summarize 组件编排：prompt 组装、run 目录产物落盘、报告与 usage 记账。

组件边界（方案设计 §0）：只依赖 transcribe.base 的数据结构与 config/errors，
不依赖 platforms / pipeline，可脱离壳工程独立使用（summarize_text 输入任意文本）。
"""

import json
import time
from importlib.resources import files
from pathlib import Path

from .. import config
from ..errors import CliError
from ..transcribe.base import Segment
from . import llm

DEFAULT_PROVIDER = "siliconflow"  # S7.3 收敛（用户实测拍板）


def load_template(name):
    """读 templates/ 下的 prompt 模板（随包分发）。"""
    return (files("bili_digest.summarize") / "templates" / name).read_text(encoding="utf-8")


def clock(sec):
    return "%02d:%02d" % (int(sec) // 60, int(sec) % 60)


def timestamped_lines(segments):
    """Segment 列表 → `[mm:ss] text` 行（模板契约的输入形态）。"""
    return [f"[{clock(s.start)}] {s.text.strip()}" for s in segments if s.text.strip()]


def format_meta(meta):
    """元信息 dict → 给 LLM 的简洁文本；只保留有值字段。"""
    meta = meta or {}
    fields = [
        ("标题", meta.get("title")),
        ("分P", meta.get("page_part") if meta.get("page", 1) and meta.get("page_part") else None),
        ("UP主" if meta.get("bvid") else "作者", meta.get("owner")),
        ("时长", f"{meta['duration']}s" if meta.get("duration") else None),
        ("简介", (meta.get("desc") or "").strip()[:300] or None),
        ("转写来源", f"{meta['engine']}（{meta['source']}）" if meta.get("engine") else None),
        ("转写疑似无语音", "是（转写可能是模型幻觉循环，报告中不要长篇复述其内容）"
         if meta.get("suspected_silent") else None),
        ("画面识别", f"{vision['model']}（{vision['frames']} 帧）"
         if (vision := meta.get("vision")) else None),
        ("画面价值门控",
         f"{meta['visual_gate']}（画面以人物口播/低视觉多样性为主，已跳过读图、未提取信息性画面）。"
         "报告需注明「画面以人物口播为主，未提取信息性画面」，不要虚构或补写画面内容"
         if meta.get("visual_gate") else None),
        ("BV号", meta.get("bvid")),
        ("笔记号", meta.get("note_id")),
    ]
    return "\n".join(f"{name}: {value}" for name, value in fields if value) or "（无元信息）"


def frame_lines(frames):
    """帧描述 → prompt 注入行（组件自持实现，不依赖 enrich 组件）。"""
    lines = []
    for f in frames:
        mark = "画面，含文字" if f.get("has_text") else "画面"
        lines.append(f"[{clock(f.get('t') or 0)}] {f.get('id')}（{mark}）{f.get('desc', '')}")
    return lines


def build_messages(text, meta=None):
    """总纲（system）+ 元信息/输出模板/（可选画面帧）/转写文本（user），单次调用出稿。"""
    meta = meta or {}
    frames = meta.get("frames") or []
    user = [
        "# 视频元信息",
        format_meta(meta),
        "",
        "# 输出模板（严格按此结构与标题层级书写；`{字段}` 用元信息或转写内容填充）",
        load_template("report-visual.md" if frames else "report.md").strip(),
    ]
    if frames:
        user += [
            "",
            "# 画面帧描述（视觉模型对视频关键帧的读图结果；id 对应 run 目录 frames/ 下同名图片）",
            *frame_lines(frames),
        ]
        if meta.get("suspected_silent"):
            user.append(
                "\n注意：本视频转写疑似无语音（ASR 幻觉），画面帧描述是主要内容来源；"
                "报告开头用引用块注明「本视频无语音，内容来自画面识别」。")
    user += [
        "",
        "# 转写文本",
        text.strip(),
    ]
    return [
        {"role": "system", "content": load_template("skill.md").strip()},
        {"role": "user", "content": "\n".join(user)},
    ]


def summarize_lines(text, meta=None, provider=DEFAULT_PROVIDER):
    """文本 + 元信息 → (Markdown 报告, usage)。CLI 与 summarize_text 共用的核心。"""
    return llm.chat(provider, build_messages(text, meta))


def summarize_text(text, meta=None, provider=DEFAULT_PROVIDER):
    """独立总结入口：任意文本 + 可选元信息 → Markdown 报告（1 次 LLM 调用）。"""
    report, _usage = summarize_lines(text, meta, provider)
    return report


def summarize_transcript(transcript, meta=None, provider=DEFAULT_PROVIDER):
    """Transcript → (Markdown 报告, usage)。usage 含 tokens 与估算成本，供 meta.json 记账。"""
    meta = dict(meta or {})
    meta.setdefault("engine", transcript.engine)
    meta.setdefault("source", transcript.source)
    meta.setdefault("suspected_silent", transcript.suspected_silent)
    text = "\n".join(timestamped_lines(transcript.segments))
    return summarize_lines(text, meta, provider)


# ---------------------------------------------------------------- run 目录产物

def new_run_dir(bvid, base=None):
    """runs/<bvid>/<YYYYmmdd-HHMMSS>/；根目录走 config.runs_dir()（不再随 cwd 漂移）。"""
    root = (Path(base) if base else config.runs_dir()) / (bvid or "unknown")
    run_dir = root / time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while run_dir.exists():
        run_dir = root / (time.strftime("%Y%m%d-%H%M%S") + f"-{n}")
        n += 1
    run_dir.mkdir(parents=True)
    return run_dir


def save_run(run_dir, files, meta):
    """写 run 目录：files 是 {文件名: 内容} 的已渲染产物；meta 单独落 meta.json。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (run_dir / name).write_text(content, encoding="utf-8")
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def load_run(run_dir):
    """读 run 目录 → (带时间戳转写文本, meta)。优先 transcript.json（有时间戳），退化 transcript.txt。"""
    run_dir = Path(run_dir)
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    jp = run_dir / "transcript.json"
    tp = run_dir / "transcript.txt"
    if jp.exists():
        data = json.loads(jp.read_text(encoding="utf-8"))
        segs = [Segment(**s) for s in data.get("segments") or []]
        if not segs:
            raise CliError(f"{jp} 里没有 segments，无法生成报告", 2)
        meta.setdefault("engine", data.get("engine"))
        meta.setdefault("source", data.get("source"))
        return "\n".join(timestamped_lines(segs)), meta
    if tp.exists():
        return tp.read_text(encoding="utf-8"), meta
    raise CliError(f"{run_dir} 下没有 transcript.json / transcript.txt，无法生成报告", 2)


def save_report(run_dir, report_md, usage):
    """写 report.md，并把 usage 记入 meta.json 的 summarize 字段（成本可见，方案设计 §5）。"""
    run_dir = Path(run_dir)
    (run_dir / "report.md").write_text(report_md, encoding="utf-8")
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["summarize"] = usage
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir / "report.md"
