"""vlm 单测：prompt 组装 / payload 结构 / JSON 解析 / usage 汇总 / 错误分类（mock HTTP，不触网）。"""

import base64
import json

import pytest

from bili_digest import config
from bili_digest.enrich import vlm
from bili_digest.enrich.frames import KeyFrame
from bili_digest.errors import CliError

JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"\x00" * 600  # JPEG magic + 填充


@pytest.fixture
def two_frames(tmp_path):
    out = []
    for fid, t in (("f00", 0.0), ("f01", 30.0)):
        p = tmp_path / f"{fid}.jpg"
        p.write_bytes(JPEG)
        out.append(KeyFrame(t=t, fid=fid, path=p))
    return out


def use_provider(monkeypatch, name="siliconflow"):
    """测试辅助：固定 VISION_PROVIDER（默认 siliconflow）并给假 key。"""
    monkeypatch.setattr(config, "get_key",
                        lambda n: {"VISION_PROVIDER": name, "VISION_MODEL": None}.get(n, "sk-test"))


def fake_post(content='{"t": 999, "desc": "经纬网示意图，标注了五带划分", "has_text": true}',
              ptok=800, ctok=120):
    calls = []

    def _post(payload, timeout=vlm.TIMEOUT, conf=None, **kw):
        calls.append(payload)
        return 200, {
            "model": payload["model"],
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": ptok, "completion_tokens": ctok},
        }

    _post.calls = calls
    return _post


# ---------------------------------------------------------------- prompt / payload

def test_build_prompt_carries_context():
    prompt = vlm.build_prompt(95.0, title="自然地理基础", owner="匠心地理")
    assert "自然地理基础" in prompt and "匠心地理" in prompt
    assert "95" in prompt and "可见文字" in prompt and "JSON" in prompt


def test_describe_frame_payload_structure(two_frames, monkeypatch):
    use_provider(monkeypatch, "siliconflow")
    post = fake_post()
    monkeypatch.setattr(vlm, "post_chat", post)
    vlm.describe_frame(two_frames[1], title="T")
    payload = post.calls[0]
    assert payload["model"] == "Qwen/Qwen3-VL-8B-Instruct"  # S7.3 默认供应商模型
    assert "thinking" not in payload  # thinking 关闭仅 zhipu 端点附加
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert base64.b64decode(content[0]["image_url"]["url"]) == JPEG  # 原始 base64，无 data URI 前缀
    assert content[1]["type"] == "text" and "T" in content[1]["text"]


# ---------------------------------------------------------------- 解析

def test_describe_frame_parses_json_and_keeps_caller_t(two_frames, monkeypatch):
    use_provider(monkeypatch)
    monkeypatch.setattr(vlm, "post_chat", fake_post())
    desc, usage = vlm.describe_frame(two_frames[1])
    assert desc == {"t": 30.0, "desc": "经纬网示意图，标注了五带划分", "has_text": True,
                    "frame_type": "other"}  # S6.4：缺失/非法 frame_type 归 other
    assert desc["t"] == 30.0  # 时间以调用方为准，不信模型回显的 999
    assert usage["prompt_tokens"] == 800 and usage["completion_tokens"] == 120
    assert usage["provider"] == "siliconflow"  # S7.4：供应商/兜底链入 usage 记账


def test_describe_frame_falls_back_to_raw_text(two_frames, monkeypatch):
    use_provider(monkeypatch)
    monkeypatch.setattr(vlm, "post_chat", fake_post(content="画面上是一幅地图"))
    desc, _ = vlm.describe_frame(two_frames[0])
    assert desc["desc"] == "画面上是一幅地图" and desc["has_text"] is True


def test_describe_frame_empty_content(two_frames, monkeypatch):
    use_provider(monkeypatch)
    monkeypatch.setattr(vlm, "post_chat", fake_post(content="  \n"))
    with pytest.raises(CliError) as err:
        vlm.describe_frame(two_frames[0])
    assert err.value.code == 4


# ---------------------------------------------------------------- 批量与 usage

def test_describe_frames_accumulates_usage(two_frames, monkeypatch, capsys):
    use_provider(monkeypatch)
    monkeypatch.setattr(vlm, "post_chat", fake_post(ptok=1000, ctok=200))
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)  # 不真等限速
    descs, usage = vlm.describe_frames(two_frames, title="T")
    assert [d["t"] for d in descs] == [0.0, 30.0]  # 与 frames 对齐
    assert usage["model"] == vlm.vision_conf()["model"]
    assert usage["provider"] == "siliconflow"
    assert usage["frames"] == 2
    assert usage["prompt_tokens"] == 2000 and usage["completion_tokens"] == 400
    assert usage["total_tokens"] == 2400
    assert usage["cost_yuan"] == vlm.estimate_cost(2000, 400)  # 免费模型 = 0
    assert "画面识别 2/2" in capsys.readouterr().err


def test_estimate_cost_provider_aware(monkeypatch):
    """S7.3：默认 siliconflow 价率未查证 → 0 估；zhipu 费率照旧。"""
    use_provider(monkeypatch, "siliconflow")
    assert vlm.estimate_cost(1_000_000, 1_000_000) == 0.0
    use_provider(monkeypatch, "zhipu")
    assert vlm.estimate_cost(1_000_000, 0) == 0.8
    assert vlm.estimate_cost(0, 1_000_000) == 2.8


def test_vision_provider_resolution(monkeypatch):
    """S7.3：VISION_PROVIDER 默认 siliconflow（Qwen3-VL）；zhipu/deepseek 覆盖与守卫。"""
    monkeypatch.setattr(config, "get_key", lambda name: None)
    conf = vlm.vision_conf()
    assert (conf["provider"], conf["model"], conf["key_env"]) == (
        "siliconflow", "Qwen/Qwen3-VL-8B-Instruct", "SILICONFLOW_API_KEY")
    use_provider(monkeypatch, "zhipu")
    assert vlm.vision_conf()["model"] == "glm-5.3-flash"
    use_provider(monkeypatch, "deepseek")
    with pytest.raises(CliError) as err:  # 官方端点无默认 VL 模型 → 用法错
        vlm.vision_conf()
    assert err.value.code == 2 and "VISION_MODEL" in str(err.value)
    monkeypatch.setattr(config, "get_key",
                        lambda n: "vl-model-x" if n == "VISION_MODEL" else "deepseek")
    assert vlm.vision_conf()["model"] == "vl-model-x"
    monkeypatch.setattr(config, "get_key",
                        lambda n: "openai" if n == "VISION_PROVIDER" else None)
    with pytest.raises(CliError) as err:
        vlm.vision_conf()
    assert err.value.code == 2


def test_1309_plan_expired_gives_specific_hint(two_frames, monkeypatch):
    """实测坑：GLM Coding Plan key 请求免费档 VLM → HTTP 429 + code 1309 套餐到期。
    必须给专属指引（而非「限流」），且不无谓重试。"""
    use_provider(monkeypatch, "zhipu")  # 1309 是智谱套餐特有错误码
    sleeps = []
    monkeypatch.setattr(vlm.time, "sleep", sleeps.append)
    monkeypatch.setattr(vlm, "post_chat",
                        lambda payload, timeout=vlm.TIMEOUT, conf=None, **kw:
                        (429, {"error": {"code": "1309", "message": "套餐已到期"}}))
    with pytest.raises(CliError) as err:
        vlm.describe_frame(two_frames[0])
    assert err.value.code == 4
    assert "Coding Plan" in str(err.value) and "VISION_MODEL" in str(err.value)
    assert sleeps == []  # 1309 不是真限流，不重试


# ---------------------------------------------------------------- 画面价值门控探测（S6.1）

def test_probe_payload_multi_image_single_call(two_frames, monkeypatch):
    """探测：≤3 帧打成一个 content 数组、单次调用；prompt 问内容类型。"""
    use_provider(monkeypatch)
    post = fake_post(content='{"type": "talking_head"}', ptok=2500, ctok=10)
    monkeypatch.setattr(vlm, "post_chat", post)
    verdict, usage = vlm.probe_content_type(two_frames, title="访谈")
    payload = post.calls[0]
    content = payload["messages"][0]["content"]
    assert len([c for c in content if c["type"] == "image_url"]) == 2  # 两帧同图组
    assert "口播" in content[-1]["text"] and "访谈" in content[-1]["text"]
    assert len(post.calls) == 1  # 单次调用（成本控制）
    assert verdict == "talking_head" and usage["prompt_tokens"] == 2500


@pytest.mark.parametrize("content, expect", [
    ('{"type": "demo"}', "demo"),
    ('{"type": "talking_head"}', "talking_head"),
    ('```json\n{"type": "talking_head"}\n```', "talking_head"),
    ('{"type": "人物口播"}', "talking_head"),   # 模型回中文枚举也能收敛
    ('{"type": "内容演示"}', "demo"),
    ('看不出来', None),                        # 判定不明确 → None（fail-open）
])
def test_probe_verdict_parsing(two_frames, monkeypatch, content, expect):
    use_provider(monkeypatch)
    monkeypatch.setattr(vlm, "post_chat", fake_post(content=content))
    verdict, _usage = vlm.probe_content_type(two_frames)
    assert verdict == expect


def test_probe_http_error_returns_none(two_frames, monkeypatch):
    """探测失败（非 200）不炸管线：返回 None 按内容型继续。"""
    use_provider(monkeypatch)
    monkeypatch.setattr(vlm, "post_chat",
                        lambda payload, timeout=vlm.TIMEOUT, conf=None, **kw: (500, {"error": {"message": "x"}}))
    verdict, usage = vlm.probe_content_type(two_frames)
    assert verdict is None and usage == {"prompt_tokens": 0, "completion_tokens": 0}


# ---------------------------------------------------------------- 错误分类（doctor 文案风格）

def test_missing_key(monkeypatch, two_frames):
    monkeypatch.setattr(config, "get_key", lambda name: None)
    with pytest.raises(CliError) as err:
        vlm.describe_frame(two_frames[0])
    assert err.value.code == 3
    assert "SILICONFLOW_API_KEY" in str(err.value) and "digest doctor" in str(err.value)


@pytest.mark.parametrize("status,keywords,code", [
    (401, ["key 无效", "digest doctor"], 3),
    (402, ["余额不足"], 3),
    (403, ["实名"], 3),
    (500, ["HTTP 500"], 4),
])
def test_error_hints(two_frames, monkeypatch, status, keywords, code):
    use_provider(monkeypatch)
    monkeypatch.setattr(vlm, "post_chat",
                        lambda payload, timeout=vlm.TIMEOUT, conf=None, **kw: (status, {"error": {"message": "x"}}))
    with pytest.raises(CliError) as err:
        vlm.describe_frame(two_frames[0])
    assert err.value.code == code
    for word in keywords:
        assert word in str(err.value)


def test_429_retries_once_then_raises(two_frames, monkeypatch):
    use_provider(monkeypatch)
    sleeps = []
    monkeypatch.setattr(vlm.time, "sleep", sleeps.append)

    def always_429(payload, timeout=vlm.TIMEOUT, conf=None, **kw):
        return 429, {}
    monkeypatch.setattr(vlm, "post_chat", always_429)
    with pytest.raises(CliError) as err:
        vlm.describe_frame(two_frames[0])
    assert "限流" in str(err.value)
    # S7.5 撤销自动兜底：单供应商内退避一次，仍 429 即报错
    assert sleeps == [vlm.RETRY_429_WAIT]


def test_429_retry_succeeds(two_frames, monkeypatch):
    use_provider(monkeypatch)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def _post(payload, timeout=vlm.TIMEOUT, conf=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return 429, {}
        return 200, {"choices": [{"message": {"content": '{"desc": "d", "has_text": false}'}}],
                     "usage": {"prompt_tokens": 5, "completion_tokens": 5}}

    monkeypatch.setattr(vlm, "post_chat", _post)
    desc, _usage = vlm.describe_frame(two_frames[0])
    assert desc["desc"] == "d" and calls["n"] == 2  # 第一次 429，退避后重试成功


def test_parse_desc_frame_type_normalized():
    """S6.4 frame_type 解析：大小写归一；非法/缺失归 other（不误伤内容帧）。"""
    ok = vlm._parse_desc('{"t": 1, "desc": "d", "has_text": true,'
                         ' "frame_type": "Desktop_Content"}', 1.0)
    assert ok["frame_type"] == "desktop_content"
    bad = vlm._parse_desc('{"t": 1, "desc": "d", "has_text": true,'
                          ' "frame_type": "weird"}', 1.0)
    assert bad["frame_type"] == "other"
    assert "frame_type" in vlm.build_prompt(1.0)  # prompt 要求输出 frame_type


def test_vision_model_override_precedence(monkeypatch):
    """S7.2/S7.3 读图模型覆盖：env BILI_DIGEST_VLM_MODEL > keys.env VISION_MODEL > 供应商默认。"""
    monkeypatch.delenv("BILI_DIGEST_VLM_MODEL", raising=False)
    monkeypatch.setattr(config, "get_key", lambda name: None)
    assert vlm.vision_conf()["model"] == "Qwen/Qwen3-VL-8B-Instruct"
    monkeypatch.setattr(config, "get_key",
                        lambda name: "glm-4.6v" if name == "VISION_MODEL" else None)
    assert vlm.vision_conf()["model"] == "glm-4.6v"  # keys.env 覆盖生效
    monkeypatch.setenv("BILI_DIGEST_VLM_MODEL", "qwen-x")
    try:
        assert vlm.vision_conf()["model"] == "qwen-x"  # 环境变量优先于 keys.env
    finally:
        monkeypatch.delenv("BILI_DIGEST_VLM_MODEL", raising=False)
        monkeypatch.setattr(config, "get_key", lambda name: None)
