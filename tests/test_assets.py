"""assets 素材包单测（S7）：assets.json 机读结构 + assets.md 人读版。"""

import json

from bili_digest import assets
from bili_digest.transcribe.base import Segment, Transcript


def _tr(segments=1, source="subtitle"):
    return Transcript([Segment(0.0, 5.0, "第%d句" % i) for i in range(segments)],
                      lang="zh", engine="subtitle", source=source, elapsed_s=1.0)


def test_write_assets_json_and_md(tmp_path):
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "f00.jpg").write_bytes(b"\xff\xd8x")
    (tmp_path / "transcript.txt").write_text("第0句", encoding="utf-8")
    meta = {"title": "测试视频", "frames": [
        {"id": "f00", "t": 65.0, "file": "frames/f00.jpg", "desc": "代码编辑器\n第二行",
         "type": "desktop_content", "has_text": True},
    ]}
    jpath, mpath = assets.write_assets(tmp_path, _tr(), meta)
    payload = json.loads(jpath.read_text(encoding="utf-8"))
    assert payload["frames"] == [
        {"file": "frames/f00.jpg", "t": 65.0, "frame_type": "desktop_content",
         "desc": "代码编辑器\n第二行"}]
    assert payload["transcript"]["segments"] == 1
    assert payload["transcript"]["source"] == "subtitle"
    assert payload["transcript"]["lang"] == "zh"
    assert payload["transcript"]["files"] == ["transcript.txt"]
    assert payload["generated_at"]

    md = mpath.read_text(encoding="utf-8")
    assert "# 素材包 · 测试视频" in md
    assert "[01:05](frames/f00.jpg)" in md and "desktop_content" in md
    assert "代码编辑器" in md and "第二行" not in md  # 描述只取首行
    assert "![65.0s](frames/f00.jpg)" in md  # 缩略图


def test_write_assets_empty_frames(tmp_path):
    jpath, mpath = assets.write_assets(tmp_path, _tr(0, source="visual-only"), {"title": None})
    payload = json.loads(jpath.read_text(encoding="utf-8"))
    assert payload["frames"] == [] and payload["transcript"]["segments"] == 0
    md = mpath.read_text(encoding="utf-8")
    assert "无帧（纯音频任务）" in md and "visual-only" in md
