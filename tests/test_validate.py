"""validate 四规则单测：各规则独立触发 + 正常样本通过 + 规则 4 标题相关性。"""

from bili_digest import validate
from bili_digest.transcribe.base import Segment


def seg(text, start, end):
    return Segment(start=start, end=end, text=text)


def zh_lines(n, duration, distinct=True):
    """n 段铺满 duration 的中文字幕。"""
    step = duration / n
    segs = []
    for i in range(n):
        text = f"第{i}段我们在讨论B站的内容生态" if distinct else "我们在讨论B站的内容生态"
        segs.append(seg(text, i * step, (i + 1) * step))
    return segs


def test_normal_subtitle_passes():
    assert validate.validate(zh_lines(10, 100.0), 100) == []


def test_rule1_coverage_triggers():
    # 只覆盖 15s / 100s
    segs = [seg(f"第{i}段我们在讨论B站的内容生态", i * 5.0, i * 5.0 + 5.0) for i in range(3)]
    failures = validate.validate(segs, 100)
    assert len(failures) == 1
    assert "时长覆盖" in failures[0]
    assert "15%" in failures[0]


def test_rule2_garbage_triggers():
    # 乱码（片假名，每行不同），覆盖充足但无中文
    garbage = "ァアィイゥウェエォオカキクケコサシスセソタチツテトナニヌネノ"
    segs = [seg(garbage[i * 5:(i + 1) * 5] * 6, i * 10.0, i * 10.0 + 10.0)
            for i in range(10)]
    failures = validate.validate(segs, 100)
    assert len(failures) == 1
    assert "中文" in failures[0]


def test_rule2_english_triggers():
    segs = [seg(f"english only line number {i} with padding words", i * 10.0, i * 10.0 + 10.0)
            for i in range(10)]
    failures = validate.validate(segs, 100)
    assert len(failures) == 1
    assert "中文" in failures[0]


def test_rule3_repetition_triggers():
    # 连续 10 行完全相同 → 重复率 9/10 = 90%
    segs = zh_lines(10, 100.0, distinct=False)
    failures = validate.validate(segs, 100)
    assert len(failures) == 1
    assert "重复" in failures[0]


def test_mixed_content_below_half_zh():
    segs = []
    for i in range(10):
        text = "中文内容" if i % 2 == 0 else "english filler text here"
        segs.append(seg(text, i * 10.0, i * 10.0 + 10.0))
    # 中文占比 ~ 40/70 < 50%
    failures = validate.validate(segs, 100)
    assert any("中文" in f for f in failures)


def test_unknown_duration_skips_coverage():
    # duration 未知时不触发覆盖规则（其余规则仍生效）
    segs = [seg(f"第{i}段我们在讨论B站的内容生态", 0.0, 1.0) for i in range(3)]
    assert validate.validate(segs, 0) == []
    assert validate.validate(segs, None) == []


def test_ratio_helpers():
    segs = zh_lines(4, 80.0)
    assert validate.coverage_ratio(segs, 80.0) == 1.0
    assert validate.dup_ratio(segs) == 0.0
    assert validate.zh_ratio(segs) > 0.5


# ---------------------------------------------------------------- 规则 4：标题相关

TITLE_12TH = "BILIBILI 12 周年演讲"
DESC_12TH = "哔哩哔哩十二周年演讲完整版，回顾社区生态与创作者故事"


def xj_tour_lines(n=20, duration=400.0):
    """S1 实测风格的无关字幕（新疆旅游）：完整、中文、不重复，但与标题无关。"""
    texts = [
        "我们今天来到了新疆的伊犁河谷", "这里的薰衣草六月份开得最好",
        "自驾走独库公路一定要注意天气", "山上随时可能下雪封路",
        "喀纳斯湖的水是碧绿色的", "图瓦人村落值得住一晚",
        "大盘鸡和烤包子一定要尝尝", "馕坑肉配上皮辣红非常解腻",
        "赛里木湖被称为大西洋最后一滴眼泪", "环湖公路大约九十公里",
        "那拉提草原可以骑马", "牧民的毡房可以借宿",
        "巴音布鲁克的九曲十八弯", "日落时分能拍到九个太阳",
        "吐鲁番的葡萄沟八月最热闹", "火焰山地表温度能到七十度",
        "坎儿井是古代的灌溉工程", "交河故城有两千多年历史",
        "乌鲁木齐的国际大巴扎", "干果和艾德莱斯绸都可以还价",
    ][:n]
    step = duration / len(texts)
    return [seg(t, i * step, (i + 1) * step) for i, t in enumerate(texts)]


def test_rule4_unrelated_subtitle_blocked():
    """S1 实际暴露的缺口：完整无关字幕（新疆旅游）× 12 周年标题，前三规则全过，规则 4 必须拦。"""
    segs = xj_tour_lines()
    assert validate.validate(segs, 400) == []  # 不带标题时四规则里前三条全过
    failures = validate.validate(segs, 400, title=TITLE_12TH, desc=DESC_12TH)
    assert len(failures) == 1
    assert "标题相关" in failures[0]


def test_rule4_related_subtitle_passes():
    texts = [f"第{i}段我们聊聊哔哩哔哩的社区生态与周年演讲" for i in range(10)]
    segs = [seg(t, i * 10.0, i * 10.0 + 10.0) for i, t in enumerate(texts)]
    assert validate.validate(segs, 100, title=TITLE_12TH, desc=DESC_12TH) == []


def test_rule4_english_title_skipped():
    segs = xj_tour_lines()
    assert validate.validate(segs, 400, title="iPhone 15 Pro Max Review", desc="specs and tests") == []


def test_rule4_none_title_backward_compatible():
    assert validate.validate(xj_tour_lines(), 400, title=None) == []


def test_rule4_desc_extends_match():
    # 标题本身与字幕无关，但简介词命中字幕 → 相关
    texts = [f"第{i}段新疆旅游喀纳斯湖攻略实录" for i in range(10)]
    segs = [seg(t, i * 10.0, i * 10.0 + 10.0) for i, t in enumerate(texts)]
    failures = validate.validate(segs, 100, title="随便什么标题", desc="新疆旅游喀纳斯湖攻略")
    assert failures == []


def test_title_overlap_helpers():
    segs = [seg("喀纳斯湖旅游攻略", 0, 5)]
    assert validate.title_overlap(segs, "iPhone 15 review") is None  # 无中文 bigram → 跳过
    assert validate.title_overlap(segs, "湖旅游") == 1.0
    assert validate.title_overlap(segs, "周年演讲") == 0.0


def test_zh_bigrams():
    assert validate.zh_bigrams("BILIBILI 12 周年演讲") == {"周年", "年演", "演讲"}
    assert validate.zh_bigrams("abc 123") == set()
    # 中文块之间的边界不产生跨块 bigram
    assert validate.zh_bigrams("周年，演讲") == {"周年", "演讲"}
