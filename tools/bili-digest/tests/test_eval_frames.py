"""eval_frames 单测（S6.4）：金标时间线 / 记分卡纯函数 + contact sheet（合成数据，无网络）。"""

import json

from bili_digest import eval_frames


def test_content_intervals_merges_and_pads():
    """同类型连续段合并为一个状态区间（类型切换即边界），stage 段不输出；
    边界外扩半个采样间隔并钳到时长。"""
    samples = [
        {"t": 0, "frame_type": "stage_talking"},
        {"t": 12, "frame_type": "desktop_content"},
        {"t": 24, "frame_type": "desktop_content"},
        {"t": 36, "frame_type": "diagram"},          # 类型切换 → 新状态
        {"t": 48, "frame_type": "stage_talking"},    # 非内容态
        {"t": 60, "frame_type": "diagram"},
    ]
    ivs = eval_frames.content_intervals(samples, interval=12, duration=72)
    assert ivs == [
        {"start": 6.0, "end": 30.0, "type": "desktop_content"},
        {"start": 30.0, "end": 42.0, "type": "diagram"},
        {"start": 54.0, "end": 66.0, "type": "diagram"},
    ]
    assert eval_frames.content_intervals([], duration=60) == []
    # type 缺失按 other（内容）计；连续缺失归同一段
    mixed = [{"t": 0, "frame_type": "desktop_content"}, {"t": 12}]
    assert eval_frames.content_intervals(mixed, interval=12, duration=24) == [
        {"start": 0.0, "end": 6.0, "type": "desktop_content"},
        {"start": 6.0, "end": 18.0, "type": "other"}]


def test_score_frames_metrics():
    gold = {"content_intervals": [{"start": 0, "end": 10}, {"start": 20, "end": 30},
                                   {"start": 40, "end": 50}]}
    kept = [
        {"t": 5, "type": "desktop_content"},    # 命中区间 1
        {"t": 8, "type": "diagram"},            # 命中区间 1（冗余）
        {"t": 25, "type": "desktop_content"},   # 命中区间 2
        {"t": 35, "type": "stage_talking"},     # 人脸帧（不参与命中）
        {"t": 100, "type": "other"},            # 未落任何区间
    ]
    card = eval_frames.score_frames(kept, gold)
    assert card["coverage"] == round(2 / 3, 3)
    assert card["precision"] == round(4 / 5, 3)   # 5 帧中 4 内容
    assert card["face_frames"] == 1
    assert card["redundancy"] == 2.0  # 全部内容帧/命中区间 = 4/2（含落空帧的摊派）
    assert card["intervals_hit"] == 2 and card["kept"] == 5
    assert card["type_distribution"] == {
        "desktop_content": 2, "diagram": 1, "stage_talking": 1, "other": 1}


def test_score_frames_degenerate_cases():
    assert eval_frames.score_frames([], {"content_intervals": [{"start": 0, "end": 5}]})[
        "precision"] is None
    assert eval_frames.score_frames([{"t": 1, "type": "other"}], {"content_intervals": []})[
        "coverage"] is None
    # 无 type 字段的旧 run 帧：按 other=内容计（配合 --score 的补分类兜底）
    card = eval_frames.score_frames([{"t": 1}], {"content_intervals": [{"start": 0, "end": 5}]})
    assert card["coverage"] == 1.0 and card["face_frames"] == 0


def test_write_sheet_renders_grid(tmp_path):
    run = tmp_path / "run"
    (run / "frames").mkdir(parents=True)
    meta = {"title": "T", "frames": [
        {"id": "f00", "t": 65, "file": "frames/f00.jpg", "desc": "代码编辑器\n第二行不入",
         "type": "desktop_content", "has_text": True},
        {"id": "f01", "t": 9, "file": "frames/f01.jpg", "desc": "舞台人像",
         "type": "stage_talking", "has_text": False},
    ]}
    (run / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    out = eval_frames.write_sheet(run)
    html = out.read_text(encoding="utf-8")
    assert out.name == "contact_sheet.html"
    assert "[01:05]" in html and "[00:09]" in html  # 时间码 mm:ss
    assert "stage_talking" in html and "desktop_content" in html
    assert "代码编辑器" in html and "第二行不入" not in html  # 描述只取首行


# ---------------------------------------------------------------- S6.5 胶水

def test_adaptive_interval():
    """金标间隔自适应：长视频 12s 封顶；短视频 duration/50 收紧，下限 1s。"""
    assert eval_frames.adaptive_interval(3390) == 12.0
    assert eval_frames.adaptive_interval(381) == round(381 / 50, 6)  # 7.62s
    assert eval_frames.adaptive_interval(95) == 1.9
    assert eval_frames.adaptive_interval(17.8) == 1.0  # 下限
    assert eval_frames.adaptive_interval(0) == 1.0


def test_redundancy_truth_pairs(monkeypatch, tmp_path):
    """同状态内逐对 pHash：≤8 记真冗余（近似重复），>8 记子状态。"""
    gold = {"content_intervals": [{"start": 0, "end": 100, "type": "desktop_content"}]}
    kept = [{"t": 10.0, "type": "desktop_content", "file": "frames/a.jpg"},
            {"t": 20.0, "type": "desktop_content", "file": "frames/b.jpg"},
            {"t": 30.0, "type": "desktop_content", "file": "frames/c.jpg"},
            {"t": 200.0, "type": "desktop_content", "file": "frames/d.jpg"}]  # 区间外不计
    # a-b 距 1（真冗余）、a-c/b-c 距 62/61（子状态）
    table = {"a": 0, "b": 1 << 40, "c": (1 << 62) - 1}

    def fake_phash(path):
        return table.get(path.stem)
    monkeypatch.setattr(eval_frames.frames_mod, "phash", fake_phash)
    out = eval_frames.redundancy_truth(tmp_path, kept, gold)
    assert out == {"pairs": 3, "true_redundant": 1, "substate": 2, "true_ratio": round(1 / 3, 3)}
