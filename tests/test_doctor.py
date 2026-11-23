"""doctor 单测：key ping 各场景诊断文本 / 本地能力 / 退出码（mock，不打真 API）。"""

import time

import pytest

from bili_digest import config, doctor
from bili_digest.errors import CliError


def fake_http(responses):
    """按 URL 分派状态码与响应体。responses: {url 片段: (status, body)}"""
    def _http(method, url, key=None, payload=None, timeout=30):
        for frag, resp in responses.items():
            if frag in url:
                return resp
        raise AssertionError(f"未预期的请求: {url}")
    return _http


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(config, "get_key", lambda name: "sk-test" if name.endswith("_API_KEY") else None)


SF_URL = doctor.SF_PING_URL
ZP_URL = doctor.ZHIPU_CHAT_URL
DS_URL = doctor.DS_BALANCE_URL


def test_ping_siliconflow_ok(monkeypatch):
    monkeypatch.setattr(doctor, "http_json",
                        fake_http({SF_URL: (200, {"data": [{"id": "m1"}, {"id": "m2"}]})}))
    assert doctor.ping_siliconflow("k") == (doctor.OK, "有效（2 个模型可见）")


def test_ping_siliconflow_401(monkeypatch):
    monkeypatch.setattr(doctor, "http_json", fake_http({SF_URL: (401, {"message": "bad"})}))
    assert doctor.ping_siliconflow("k") == (doctor.FAIL, "key 无效（或端点错：中国站密钥须配 .cn 端点）")


def test_ping_zhipu_429_with_reason(monkeypatch):
    monkeypatch.setattr(doctor, "http_json", fake_http(
        {ZP_URL: (429, {"error": {"code": "1309", "message": "套餐已到期"}})}))
    state, detail = doctor.ping_zhipu("k")
    assert state == doctor.FAIL
    assert "被限流：套餐已到期" in detail


def test_ping_zhipu_402(monkeypatch):
    monkeypatch.setattr(doctor, "http_json", fake_http({ZP_URL: (402, {"error": {}})}))
    assert doctor.ping_zhipu("k") == (doctor.FAIL, "余额不足（欠费）")


def test_ping_deepseek_ok_with_balance(monkeypatch):
    monkeypatch.setattr(doctor, "http_json",
                        fake_http({DS_URL: (200, {"balance_infos": [
                            {"currency": "CNY", "total_balance": "9.50"}]})}))
    assert doctor.ping_deepseek("k") == (doctor.OK, "有效（余额 9.50 元）")


def test_check_key_network_error(monkeypatch):
    monkeypatch.setattr(config, "get_key", lambda name: "sk-test")
    def boom(*a, **kw):
        raise CliError("网络不可用或超时: xxx", 4)
    monkeypatch.setattr(doctor, "http_json", boom)
    state, status_text, action = doctor.check_key("DEEPSEEK_API_KEY", "DeepSeek key", "https://x.com")
    assert state == doctor.FAIL
    assert "网络" in status_text


def test_check_key_missing(monkeypatch):
    monkeypatch.setattr(config, "get_key", lambda name: None)
    state, status_text, action = doctor.check_key("ZHIPU_API_KEY", "智谱 key", "https://x.cn")
    assert state == doctor.WARN
    assert status_text == "未配置"
    assert "keys.env" in action


def test_check_key_401_action_points_to_console(monkeypatch):
    monkeypatch.setattr(config, "get_key", lambda name: "sk-bad")
    monkeypatch.setattr(doctor, "http_json", fake_http({ZP_URL: (401, {})}))
    state, status_text, action = doctor.check_key("ZHIPU_API_KEY", "智谱 key", "https://console.example")
    assert state == doctor.FAIL
    assert "key 无效" in status_text
    assert "https://console.example" in action


SCAN_CRED = {"cookies": {"SESSDATA": "s", "DedeUserID": "42"}, "refresh_token": "r"}


def _login_state(source, logged_in, cred=None):
    return {"source": source, "logged_in": logged_in, "uname": "测试用户" if logged_in else None,
            "mid": 42 if logged_in else None, "cred": cred}


def test_check_bili_login_no_cred(monkeypatch):
    """无任何凭据：WARN，文案与指引同前（扫码 / 插件 Cookie 两条出路）。"""
    monkeypatch.setattr(doctor.bilibili, "login_state", lambda: _login_state(None, False))
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert cap == "B站登录态" and name == "凭据文件" and state == doctor.WARN
    assert "未登录" in status_text and "ASR 路径不受影响" in status_text
    assert "digest login" in action and "BILI_COOKIE" in action


def test_check_bili_login_scan_cred_valid(monkeypatch):
    """扫码凭据有效：OK，带 mid 与已用天数；不再出现「自动续期可用」，下一步点明失效出路。"""
    cred = dict(SCAN_CRED, login_at=int(time.time()) - 3 * 86400)
    monkeypatch.setattr(doctor.bilibili, "login_state",
                        lambda: _login_state("credentials.json", True, cred))
    monkeypatch.setattr(doctor.bilibili, "cookie_from_keys", lambda: None)
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert (cap, name, state) == ("B站登录态", "扫码凭据", doctor.OK)
    assert "mid 42" in status_text and "3 天" in status_text
    assert "自动续期不可用" in status_text and "自动续期可用" not in status_text
    assert "失效后需重新登录" in status_text
    assert "失效后" in action and "digest login" in action


def test_check_bili_login_scan_cred_expired(monkeypatch):
    """扫码凭据失效：WARN（不是 FAIL，ASR 不受影响）；已配 BILI_COOKIE 时指路删凭据文件改用。"""
    monkeypatch.setattr(doctor.bilibili, "login_state",
                        lambda: _login_state("credentials.json", False, SCAN_CRED))
    monkeypatch.setattr(doctor.bilibili, "cookie_from_keys", lambda: "SESSDATA=plugin")
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert (cap, name, state) == ("B站登录态", "扫码凭据", doctor.WARN)
    assert "已失效" in status_text
    assert "digest login" in action and f"删除 {doctor.bilibili.CRED_PATH}" in action


def test_check_bili_login_plugin_cookie(monkeypatch):
    """仅插件 Cookie：有效 OK / 失效 WARN；无凭据天数，出路是回插件重新导出。"""
    monkeypatch.setattr(doctor.bilibili, "login_state", lambda: _login_state("BILI_COOKIE", True))
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert (cap, name, state) == ("B站登录态", "插件 Cookie", doctor.OK)
    assert "mid 42" in status_text and "天" not in status_text
    assert "自动续期不可用" in status_text
    assert "Chrome 插件" in action and "digest login" in action

    monkeypatch.setattr(doctor.bilibili, "login_state", lambda: _login_state("BILI_COOKIE", False))
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert (cap, name, state) == ("B站登录态", "插件 Cookie", doctor.WARN)
    assert "已失效" in status_text and "Chrome 插件" in action


def test_check_bili_login_network_error_not_reported_expired(monkeypatch):
    """实测遇网络/风控：WARN「无法验证登录态」+ 稍后重试，不误报成登录失效、也不是 FAIL。"""
    def boom():
        raise CliError("网络不可用: x", 4)

    monkeypatch.setattr(doctor.bilibili, "login_state", boom)
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert cap == "B站登录态" and state == doctor.WARN
    assert "无法验证登录态" in status_text and "已失效" not in status_text
    assert action == "稍后重试 digest doctor"


def test_check_bili_login_corrupted_cred_is_fail(monkeypatch):
    """凭据文件损坏（code 3）不是网络问题：仍 FAIL 并指路重新登录，而不是「稍后重试」。"""
    def boom():
        raise CliError("凭据文件损坏，请重新 digest login: x", 3)

    monkeypatch.setattr(doctor.bilibili, "login_state", boom)
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert (cap, name, state) == ("B站登录态", "凭据文件", doctor.FAIL)
    assert "凭据文件损坏" in status_text and action == "重新执行 digest login"


class ReadTimeoutResp:
    """urlopen 返回的上下文管理器：读阶段抛 TimeoutError（不是 URLError）。"""
    status, headers = 200, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        raise TimeoutError("timed out")


def test_check_bili_login_read_timeout_not_crash(monkeypatch):
    """读阶段超时经 http() 转成 CliError：落 WARN「无法验证登录态」，而不是裸抛让 doctor 崩溃。"""
    monkeypatch.setattr(doctor.bilibili, "load_cred", lambda: {"cookies": {"SESSDATA": "s"}})
    monkeypatch.setattr(doctor.bilibili.urllib.request, "urlopen",
                        lambda req, timeout=20: ReadTimeoutResp())
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert cap == "B站登录态" and state == doctor.WARN
    assert "无法验证登录态" in status_text and action == "稍后重试 digest doctor"


def test_run_capability_grouping_good_and_bad(monkeypatch):
    """S7.3 供应商收敛：读图/总结/云转写全绑硅基流动（一把 key）；坏 key 时三能力同 warn；
    智谱/DeepSeek key 已配置时显示「可选备用」行，未配置不显示。"""
    monkeypatch.setattr(doctor, "check_local", lambda: [])
    monkeypatch.setattr(doctor, "check_key",
                        lambda name, purpose, console: (doctor.OK, "实测通过", "无需处理"))
    monkeypatch.setattr(doctor.config, "get_key", lambda name: None)  # 无备用 key
    rows = doctor.run()
    assert [r[0] for r in rows] == ["读图能力", "总结能力", "云端转写"]
    assert all(r[2] == doctor.OK for r in rows)
    assert "硅基流动 key" in rows[0][1] and "一把 key 全搞定" in rows[1][1]
    assert "Qwen/Qwen3-VL-8B-Instruct" in rows[0][1]  # 默认读图模型随供应商

    monkeypatch.setattr(doctor, "check_key",
                        lambda name, purpose, console: (doctor.WARN, "未配置", "按需申请"))
    rows_bad = doctor.run()
    assert all(r[2] == doctor.WARN for r in rows_bad[:3])  # 三能力同源同态

    monkeypatch.setattr(doctor.config, "get_key",
                        lambda name: "FAKE" if name in ("ZHIPU_API_KEY", "DEEPSEEK_API_KEY")
                        else None)
    rows_bak = doctor.run()
    backup = [r for r in rows_bak if r[0] == "可选备用"]
    assert len(backup) == 2 and all(r[2] == doctor.OK for r in backup)
    assert "智谱 key" in backup[0][1] and "DeepSeek key" in backup[1][1]


def test_render_table_marks():
    table = doctor.render_table([
        ("读图能力", "智谱 key", doctor.OK, "正常", "无需处理"),
        ("总结能力", "智谱 key", doctor.WARN, "未配置", "去申请"),
        ("总结能力", "DeepSeek key", doctor.FAIL, "key 无效", "重新生成"),
    ])
    assert "【读图能力】" in table and "【总结能力】" in table
    assert "状态" in table and "下一步动作" in table
    assert "✔ 智谱 key" in table and "△ 智谱 key" in table and "✘ DeepSeek key" in table


def test_main_exit_code(monkeypatch, capsys):
    # 全绿：三 key ok + 本地能力全 ok → 0
    monkeypatch.setattr(doctor, "run", lambda: [
        ("读图能力", "k1", doctor.OK, "有效", "无需处理"),
        ("总结能力", "k2", doctor.OK, "有效", "无需处理"),
        ("云端转写", "k3", doctor.OK, "有效", "无需处理"),
        ("本地转写", "mlx", doctor.OK, "可用", "无需处理"),
        ("基础依赖", "ffmpeg", doctor.OK, "已安装", "无需处理"),
        ("B站登录态", "login", doctor.OK, "已登录", "无需处理"),
    ])
    assert doctor.main() == 0
    out_text = capsys.readouterr().out
    assert "体检结论：无异常" in out_text
    # 有红项 → 1
    monkeypatch.setattr(doctor, "run", lambda: [
        ("读图能力", "k1", doctor.FAIL, "key 无效", "重新生成")])
    assert doctor.main() == 1
    assert "1 项异常" in capsys.readouterr().out


def test_main_warn_count(monkeypatch, capsys):
    """warn 计数取状态列：1 条 WARN → 「1 项可按需优化」，没有则 0。"""
    rows = [("读图能力", "k1", doctor.OK, "有效", "无需处理"),
            ("B站登录态", "扫码凭据", doctor.WARN, "登录态已失效", "重新执行 digest login")]
    monkeypatch.setattr(doctor, "run", lambda: rows)
    assert doctor.main() == 0
    assert "体检结论：无异常（1 项可按需优化）" in capsys.readouterr().out
    monkeypatch.setattr(doctor, "run", lambda: rows[:1])
    assert doctor.main() == 0
    assert "体检结论：无异常（0 项可按需优化）" in capsys.readouterr().out
