"""fusion 单测：时间轴归并 / enriched_transcript.md 渲染 / prompt 注入行（纯函数，无 IO）。"""

from bili_digest.enrich import fusion
from bili_digest.transcribe.base import Segment

SEGS = [
    Segment(0.0, 4.0, "大家好"),
    Segment(10.0, 14.0, "现在讲经纬网"),
    Segment(200.0, 205.0, ""),
]
FRAMES = [
    {"id": "f00", "t": 8.0, "file": "frames/f00.jpg", "desc": "封面：自然地理基础", "has_text": True},
    {"id": "f01", "t": 195.5, "file": "frames/f01.jpg", "desc": "地球五带划分示意图", "has_text": True},
]
META = {"title": "自然地理基础", "note_id": "6aa6", "duration": 200}


def test_build_timeline_interleaves_by_time():
    timeline = fusion.build_timeline(SEGS, FRAMES)
    assert [(int(t), kind) for t, kind, _ in timeline] == [
        (0, "speech"), (8, "frame"), (10, "speech"), (195, "frame")]  # 空文本段被剔除


def test_render_enriched_md_format():
    md = fusion.render_enriched_md(SEGS, FRAMES, META)
    assert "# 自然地理基础 · 富化时间轴" in md
    assert "[00:00] 🗣 大家好" in md
    assert "[00:08] 🖼 （画面）封面：自然地理基础" in md
    assert "![f00](frames/f00.jpg)" in md            # 帧以相对路径引用
    assert "[03:15] 🖼" in md                          # 195.5s → mm:ss
    assert "大家好" in md and "经纬网" in md


def test_render_enriched_md_frames_only():
    """零语音视频：无有效段，只剩画面行（杀手锏案例形态）。"""
    md = fusion.render_enriched_md([Segment(0, 1, "")], FRAMES, META)
    assert "] 🗣" not in md        # 无有效语音段（图例行除外）
    assert md.count("] 🖼") == 2


def test_render_enriched_md_no_frames():
    md = fusion.render_enriched_md(SEGS, [], META)
    assert "（画面）" not in md and "] 🗣" in md


def test_frame_lines_for_prompt():
    lines = fusion.frame_lines_for_prompt(FRAMES)
    assert lines == [
        "[00:08] f00（画面，含文字）封面：自然地理基础",
        "[03:15] f01（画面，含文字）地球五带划分示意图",
    ]
    no_text = [{"id": "f02", "t": 61, "desc": "风景镜头", "has_text": False}]
    assert fusion.frame_lines_for_prompt(no_text) == ["[01:01] f02（画面）风景镜头"]
