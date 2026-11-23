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


class ReadTimeoutResp:
    """urlopen 返回的上下文管理器：读阶段抛 TimeoutError（不是 URLError）。"""
    status, headers = 200, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        raise TimeoutError("timed out")


def test_http_read_timeout_is_cli_error(monkeypatch):
    """读阶段的 TimeoutError 不是 URLError：同样转成「网络不可用」（code 4），不能裸抛。"""
    monkeypatch.setattr(bilibili.urllib.request, "urlopen", lambda req, timeout=20: ReadTimeoutResp())
    with pytest.raises(CliError) as err:
        bilibili.api_json(f"{bilibili.API}/x/web-interface/nav", cookie="SESSDATA=s")
    assert err.value.code == 4


SCAN_CRED = {"cookies": {"SESSDATA": "s", "bili_jct": "j"}, "refresh_token": "r", "login_at": 1700000000}


def fake_nav(logged_in=True):
    """替换 api_json：记录收到的 cookie，返回 nav 风格响应。"""
    seen = {}

    def _api_json(url, cookie=None, form=None, referer=bilibili.WEB + "/"):
        seen["url"], seen["cookie"] = url, cookie
        if logged_in:
            return {"code": 0, "data": {"uname": "测试用户", "mid": 42}}
        return {"code": -101, "message": "账号未登录", "data": {"isLogin": False}}

    return seen, _api_json


def test_login_state_scan_cred_first(monkeypatch):
    """扫码凭据优先：用它的 cookies 打 nav，不去看插件 Cookie。"""
    seen, api_json = fake_nav()
    monkeypatch.setattr(bilibili, "load_cred", lambda: SCAN_CRED)
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: "SESSDATA=plugin")
    monkeypatch.setattr(bilibili, "api_json", api_json)
    state = bilibili.login_state()
    assert seen["url"] == f"{bilibili.API}/x/web-interface/nav"
    assert seen["cookie"] == "SESSDATA=s; bili_jct=j"  # 与管线同一套 cookie_header
    assert state == {"source": "credentials.json", "logged_in": True, "uname": "测试用户",
                     "mid": 42, "cred": SCAN_CRED}


def test_login_state_scan_cred_expired(monkeypatch):
    _, api_json = fake_nav(logged_in=False)
    monkeypatch.setattr(bilibili, "load_cred", lambda: SCAN_CRED)
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: "SESSDATA=plugin")
    monkeypatch.setattr(bilibili, "api_json", api_json)
    state = bilibili.login_state()
    assert state["source"] == "credentials.json" and state["logged_in"] is False


def test_login_state_falls_back_to_plugin_cookie(monkeypatch):
    """无扫码凭据 → 回退插件 BILI_COOKIE 原始串。"""
    seen, api_json = fake_nav()
    monkeypatch.setattr(bilibili, "load_cred", lambda: None)
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: "SESSDATA=plugin")
    monkeypatch.setattr(bilibili, "api_json", api_json)
    state = bilibili.login_state()
    assert seen["cookie"] == "SESSDATA=plugin"
    assert state == {"source": "BILI_COOKIE", "logged_in": True, "uname": "测试用户",
                     "mid": 42, "cred": None}


def test_login_state_plugin_cookie_expired(monkeypatch):
    _, api_json = fake_nav(logged_in=False)
    monkeypatch.setattr(bilibili, "load_cred", lambda: None)
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: "SESSDATA=plugin")
    monkeypatch.setattr(bilibili, "api_json", api_json)
    state = bilibili.login_state()
    assert state["source"] == "BILI_COOKIE" and state["logged_in"] is False


def test_login_state_unconfigured_makes_no_request(monkeypatch):
    def must_not_call(*a, **kw):
        raise AssertionError("未配置任何凭据时不应打接口")

    monkeypatch.setattr(bilibili, "load_cred", lambda: None)
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: None)
    monkeypatch.setattr(bilibili, "api_json", must_not_call)
    assert bilibili.login_state() == {"source": None, "logged_in": False, "uname": None,
                                      "mid": None, "cred": None}


def test_login_state_network_error_propagates(monkeypatch):
    """网络/风控 CliError 原样上抛，由调用方呈现（不吞成「未登录」）。"""
    def boom(*a, **kw):
        raise CliError("网络不可用: x", 4)

    monkeypatch.setattr(bilibili, "load_cred", lambda: SCAN_CRED)
    monkeypatch.setattr(bilibili, "api_json", boom)
    with pytest.raises(CliError) as err:
        bilibili.login_state()
    assert err.value.code == 4


def test_login_hint_by_source(monkeypatch):
    """出路按凭据来源分三种；扫码凭据失效且已配 BILI_COOKIE 时给出改用方法。"""
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: None)
    none_hint = bilibili.login_hint(None)
    assert "digest login" in none_hint and "BILI_COOKIE" in none_hint
    assert bilibili.login_hint("credentials.json") == "重新执行 digest login 扫码"
    plugin_hint = bilibili.login_hint("BILI_COOKIE")
    assert "Chrome 插件" in plugin_hint and "digest login" in plugin_hint
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: "SESSDATA=plugin")
    hint = bilibili.login_hint("credentials.json")
    assert hint.startswith("重新执行 digest login 扫码；")
    assert f"删除 {bilibili.CRED_PATH}" in hint and "优先生效" in hint
