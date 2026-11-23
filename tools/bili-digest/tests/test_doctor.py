"""doctor 单测：key ping 各场景诊断文本 / 本地能力 / 退出码（mock，不打真 API）。"""

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


def test_check_bili_login_states(monkeypatch):
    monkeypatch.setattr(doctor.bilibili, "load_cred", lambda: None)
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert cap == "B站登录态" and state == doctor.WARN
    assert "digest login" in action
    monkeypatch.setattr(doctor.bilibili, "load_cred",
                        lambda: {"cookies": {"DedeUserID": "42"}, "login_at": 0, "refresh_token": "r"})
    cap, name, state, status_text, action = doctor.check_bili_login()
    assert state == doctor.OK
    assert "mid 42" in status_text


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
