"""字幕/transcript 健全性校验（防风控污染，方案设计 §5）。

四规则，任一不过视为「疑似污染字幕」，调用方应记 warning 并降级走 ASR（不报错）：
1. 时长覆盖：字幕段总时长 ≥ 视频时长 60%
2. 语言一致：中文字符占比 ≥ 50%（zh 视频假设）
3. 重复率：连续完全相同的行占比 < 30%
4. 标题相关（S2 补）：视频标题+简介的中文 bigram 出现在字幕文本中的比例 ≥ 15%
   （拦「完整但无关」的风控污染字幕，S1 实测 P1 曾拿到无关节目字幕）；
   标题/简介提取不出中文 bigram（全英文、数字）时跳过本规则
"""

import re

from .transcribe.base import Segment

ZH_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
MIN_COVERAGE = 0.6
MIN_ZH_RATIO = 0.5
MAX_DUP_RATIO = 0.3
MIN_TITLE_OVERLAP = 0.15
DESC_LIMIT = 200  # 简介只取开头，防 UP 主小作文稀释 bigram


def coverage_ratio(segments, duration):
    """字幕段总时长 / 视频时长；duration 未知（<=0）返回 None（跳过该规则）。"""
    if duration is None or duration <= 0:
        return None
    covered = sum(max(0.0, seg.end - seg.start) for seg in segments)
    return min(covered / duration, 1.0)


def zh_ratio(segments):
    chars = "".join(seg.text for seg in segments)
    total = sum(1 for c in chars if not c.isspace())
    if not total:
        return 0.0
    return len(ZH_RE.findall(chars)) / total


def dup_ratio(segments):
    texts = [seg.text.strip() for seg in segments if seg.text.strip()]
    if not texts:
        return 0.0
    dups = sum(1 for i in range(1, len(texts)) if texts[i] == texts[i - 1])
    return dups / len(texts)


def zh_bigrams(text):
    """中文 bigram 集合：先按连续中文分块，块内两两组合。"""
    grams = set()
    for block in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", text):
        grams.update(block[i:i + 2] for i in range(len(block) - 1))
    return grams


def title_overlap(segments, title, desc=""):
    """标题+简介的中文 bigram 出现在字幕文本中的比例；无中文 bigram 返回 None（跳过规则）。"""
    grams = zh_bigrams(f"{title or ''} {(desc or '')[:DESC_LIMIT]}")
    if not grams:
        return None
    sub_grams = zh_bigrams("".join(seg.text for seg in segments))
    return len(grams & sub_grams) / len(grams)


def validate(segments: list[Segment], duration, title=None, desc="") -> list[str]:
    """返回未通过的规则描述列表；空列表 = 校验通过。title 为 None 时跳过规则 4。"""
    failures = []
    cov = coverage_ratio(segments, duration)
    if cov is not None and cov < MIN_COVERAGE:
        failures.append(f"时长覆盖 {cov:.0%} < {MIN_COVERAGE:.0%}")
    zh = zh_ratio(segments)
    if zh < MIN_ZH_RATIO:
        failures.append(f"中文字符占比 {zh:.0%} < {MIN_ZH_RATIO:.0%}")
    dup = dup_ratio(segments)
    if dup >= MAX_DUP_RATIO:
        failures.append(f"连续重复行占比 {dup:.0%} >= {MAX_DUP_RATIO:.0%}")
    if title is not None:
        overlap = title_overlap(segments, title, desc)
        if overlap is not None and overlap < MIN_TITLE_OVERLAP:
            failures.append(f"标题相关 {overlap:.0%} < {MIN_TITLE_OVERLAP:.0%}")
    return failures
