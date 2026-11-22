"""platforms/bilibili 纯函数单测（不触网）。"""

import pytest

from bili_digest.errors import CliError
from bili_digest.platforms import bilibili


def test_mixin_key_known_vector():
    # B站 WBI 官方文档示例密钥对
    img_key = "7cd084941338484aae1ad9425b84077c"
    sub_key = "4932caff0ff746eab6f01bf08b70ac45"
    assert bilibili.mixin_key(img_key, sub_key) == "ea1db124af3c7062474693fa704f4ff8"


def test_wbi_sign_deterministic():
    key = "ea1db124af3c70624746993fa704f4ff8"
    q1 = bilibili.wbi_sign({"bvid": "BV1CV411s7jd", "cid": 123}, key, now=1702204800)
    q2 = bilibili.wbi_sign({"bvid": "BV1CV411s7jd", "cid": 123}, key, now=1702204800)
    assert q1 == q2
    assert "wts=1702204800" in q1
    rid = dict(p.split("=", 1) for p in q1.split("&"))["w_rid"]
    assert len(rid) == 32
    int(rid, 16)  # 合法 hex


def test_wbi_sign_strips_special_chars():
    key = "ea1db124af3c70624746993fa704f4ff8"
    q = bilibili.wbi_sign({"k": "a!'()*b"}, key, now=1702204800)
    assert "k=ab" in q


def test_resolve_bvid_bv():
    assert bilibili.resolve_bvid("BV1CV411s7jd") == "BV1CV411s7jd"
    assert bilibili.resolve_bvid("https://www.bilibili.com/video/BV1CV411s7jd?p=5") == "BV1CV411s7jd"
    assert bilibili.resolve_bvid("看这个 BV1CV411s7jd 不错") == "BV1CV411s7jd"


def test_resolve_bvid_unrecognized():
    with pytest.raises(CliError) as err:
        bilibili.resolve_bvid("hello world")
    assert err.value.code == 2


def test_page_hint():
    assert bilibili.page_hint("https://www.bilibili.com/video/BV1CV411s7jd?p=5") == 5
    assert bilibili.page_hint("https://www.bilibili.com/video/BV1CV411s7jd?p=12&x=1") == 12
    assert bilibili.page_hint("https://www.bilibili.com/video/BV1CV411s7jd") is None
    assert bilibili.page_hint("BV1CV411s7jd") is None


def test_pick_track_pref_and_want():
    tracks = [{"lan": "ai-zh"}, {"lan": "zh-CN"}]
    assert bilibili.pick_track(tracks)["lan"] == "zh-CN"  # 人工字幕优先于 AI
    assert bilibili.pick_track(tracks, want="ai-zh")["lan"] == "ai-zh"
    assert bilibili.pick_track([]) is None
    assert bilibili.pick_track([{"lan": "ja"}])["lan"] == "ja"  # 无偏好命中取第一条


class StubHeaders:
    def __init__(self, cookies):
        self._cookies = cookies

    def get_all(self, name):
        return self._cookies if name == "Set-Cookie" else None


def test_parse_set_cookie():
    headers = StubHeaders([
        "SESSDATA=abc%2Cdef; Path=/; HttpOnly",
        "bili_jct=jct123; Path=/",
        "ignored=value; Path=/",
    ])
    assert bilibili.parse_set_cookie(headers) == {"SESSDATA": "abc%2Cdef", "bili_jct": "jct123"}


def test_cookie_header_filters():
    cookies = {"SESSDATA": "s", "bili_jct": "j", "other": "x"}
    header = bilibili.cookie_header(cookies)
    assert header == "SESSDATA=s; bili_jct=j"
