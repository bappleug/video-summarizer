"""素材包（S7 一等交付物）：assets.json（机读）+ assets.md（人读）。

既有产物（report / transcript.* / frames/ / enriched_transcript.md）全部不动，
本模块只增不删。assets.json 供 MCP get_assets 直接回放；assets.md 给人看：
帧缩略图表格（时间/类型/描述首行）+ transcript 概要。
"""

import json
import time
from pathlib import Path

from .errors import log


def write_assets(run_dir, transcript, meta) -> tuple[Path, Path]:
    """run 目录 → (assets.json, assets.md)。frames 来自 meta['frames']（含 type/desc）。"""
    run_dir = Path(run_dir)
    frames = [{"file": f["file"], "t": f["t"], "frame_type": f.get("type") or "other",
               "desc": f.get("desc") or ""} for f in meta.get("frames") or []]
    payload = {
        "frames": frames,
        "transcript": {
            "files": [n for n in ("transcript.txt", "transcript.md", "transcript.json",
                                  "enriched_transcript.md") if (run_dir / n).exists()],
            "segments": len(transcript.segments),
            "source": transcript.source,
            "lang": transcript.lang,
        },
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    jpath = run_dir / "assets.json"
    jpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for f in frames:
        mm, ss = divmod(int(f["t"]), 60)
        desc = f["desc"].strip().splitlines()[0][:100] if f["desc"].strip() else ""
        rows.append(f"| [{mm:02d}:{ss:02d}]({f['file']}) | {f['frame_type']} | {desc} |")
    tr = payload["transcript"]
    md = [
        f"# 素材包 · {meta.get('title') or run_dir.name}",
        f"> 生成于 {payload['generated_at']} · {len(frames)} 帧 · transcript "
        f"{tr['source']}（{tr['segments']} 段，{tr['lang'] or '?'}）",
        "",
        "## 帧清单",
        "",
        "| 时间 | 类型 | 描述 |",
        "|---|---|---|",
        *(rows or ["| - | - | 无帧（纯音频任务） |"]),
        "",
        "## 缩略图",
        "",
        *[f"![{f['t']}s]({f['file']})" for f in frames],
        "",
        "## transcript 概要",
        "",
        f"- 来源：{tr['source']} · {tr['segments']} 段 · 语言 {tr['lang'] or '?'}",
        f"- 文件：{'、'.join(tr['files']) or '无'}",
        "",
    ]
    mpath = run_dir / "assets.md"
    mpath.write_text("\n".join(md), encoding="utf-8")
    log(f"素材包 → {jpath} / {mpath}（{len(frames)} 帧）")
    return jpath, mpath
