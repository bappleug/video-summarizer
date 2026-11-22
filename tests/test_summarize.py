"""summarize 组件单测：llm client / prompt 组装 / run 目录产物（mock HTTP，不触网）。

含验收 5 的独立使用单测：summarize_text 脱离 pipeline 直接出报告。
"""

import json

import pytest

from bili_digest import config
from bili_digest.errors import CliError
from bili_digest.summarize import llm, report
from bili_digest.summarize.report import summarize_text
from bili_digest.transcribe.base import Segment, Transcript


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(config, "get_key", lambda name: "sk-test" if name.endswith("_API_KEY") else None)


def fake_post_chat(content="# 报告\n正文", ptok=1000, ctok=500, model=None):
    def _post(provider, messages, temperature=0.3, timeout=180):
        return 200, {
            "model": model or llm.PROVIDERS[provider]["model"],
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": ptok, "completion_tokens": ctok},
        }
    return _post


def make_transcript():
    segs = [Segment(i * 30.0, i * 30.0 + 30.0, f"第{i}句内容") for i in range(4)]
    return Transcript(segs, lang="zh", engine="mlx-whisper", source="asr", elapsed_s=1.0)


META = {"bvid": "BV1CV411s7jd", "title": "测试视频", "owner": "UP", "duration": 120, "desc": "简介"}


# ---------------------------------------------------------------- llm.py

def test_estimate_cost_constants():
    # 费率常量（元/百万 tokens，查证 2026-11-20）：zhipu 0.8/2.8、deepseek 空闲档 1/4
    assert llm.estimate_cost("zhipu", 1_000_000, 1_000_000) == pytest.approx(3.6)
    assert llm.estimate_cost("deepseek", 1_000_000, 1_000_000) == pytest.approx(5.0)
    assert llm.estimate_cost("zhipu", 0, 0) == 0


def test_chat_returns_content_and_usage(with_key, monkeypatch):
    monkeypatch.setattr(llm, "post_chat", fake_post_chat(ptok=1200, ctok=800))
    content, usage = llm.chat("zhipu", [{"role": "user", "content": "hi"}])
    assert content == "# 报告\n正文"
    assert usage["provider"] == "zhipu"
    assert usage["model"] == "glm-5.3-flash"
    assert usage["prompt_tokens"] == 1200
    assert usage["total_tokens"] == 2000
    assert usage["cost_yuan"] == pytest.approx(1200 / 1e6 * 0.8 + 800 / 1e6 * 2.8)


def test_chat_unknown_provider():
    with pytest.raises(CliError) as err:
        llm.chat("openai", [])
    assert err.value.code == 2


def test_chat_no_key(monkeypatch):
    monkeypatch.setattr(config, "get_key", lambda name: None)
    with pytest.raises(CliError) as err:
        llm.chat("zhipu", [])
    assert err.value.code == 3
    assert "ZHIPU_API_KEY" in str(err.value) and "digest doctor" in str(err.value)


@pytest.mark.parametrize("status,keywords,code", [
    (401, ["key 无效", "digest doctor"], 3),
    (402, ["余额不足", "充值"], 3),
    (403, ["实名"], 3),
    (429, ["限流"], 4),
])
def test_chat_error_hints(with_key, monkeypatch, status, keywords, code):
    def _post(provider, messages, temperature=0.3, timeout=180):
        return status, {"error": {"message": "boom"}}
    monkeypatch.setattr(llm, "post_chat", _post)
    with pytest.raises(CliError) as err:
        llm.chat("deepseek", [])
    assert err.value.code == code
    for word in keywords:
        assert word in str(err.value)


def test_chat_network_error(monkeypatch):
    def boom(*a, **kw):
        raise CliError("LLM 网络不可用或超时: x", 4)
    monkeypatch.setattr(llm, "post_chat", boom)
    with pytest.raises(CliError) as err:
        llm.chat("zhipu", [])
    assert err.value.code == 4


def test_chat_deepseek_provider(with_key, monkeypatch):
    monkeypatch.setattr(llm, "post_chat", fake_post_chat(model="deepseek-flash"))
    _, usage = llm.chat("deepseek", [])
    assert usage["model"] == "deepseek-flash"
    assert usage["cost_yuan"] == pytest.approx(1000 / 1e6 * 1.0 + 500 / 1e6 * 4.0)


# ---------------------------------------------------------------- report.py

def test_templates_shipped():
    skill = report.load_template("skill.md")
    assert "事实边界" in skill and "[mm:ss]" in skill
    assert "{title}" in report.load_template("report.md")


def test_build_messages_structure():
    messages = report.build_messages("[00:00] 第一句", META)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "视频内容分析师" in messages[0]["content"]
    user = messages[1]["content"]
    assert "测试视频" in user and "UP" in user and "[00:00] 第一句" in user
    assert "输出模板" in user


def test_summarize_text_independent(with_key, monkeypatch):
    """验收 5：summarize 组件独立使用——任意文本（无 transcript/壳依赖）→ 报告。"""
    monkeypatch.setattr(llm, "post_chat", fake_post_chat(content="# 独立报告\n要点"))
    result = summarize_text("任意文本内容，比如会议纪要", meta={"title": "会议"})
    assert result == "# 独立报告\n要点"


def test_summarize_transcript(with_key, monkeypatch):
    captured = {}

    def _post(provider, messages, temperature=0.3, timeout=180):
        captured["user"] = messages[1]["content"]
        return 200, {
            "model": "glm-5.3-flash",
            "choices": [{"message": {"content": "R"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    monkeypatch.setattr(llm, "post_chat", _post)
    md, usage = report.summarize_transcript(make_transcript(), META)
    assert md == "R"
    assert usage["total_tokens"] == 15
    assert "[00:00] 第0句内容" in captured["user"]  # 时间戳行进 prompt
    assert "[01:30] 第3句内容" in captured["user"]
    assert "mlx-whisper" in captured["user"]  # engine 已并入元信息
    assert "转写疑似无语音" not in captured["user"]  # 正常转写不注明


def test_summarize_transcript_prompt_notes_suspected_silence(with_key, monkeypatch):
    """疑似无语音 → prompt 元信息注明，报告不做长篇幻觉复述。"""
    captured = {}

    def _post(provider, messages, temperature=0.3, timeout=180):
        captured["user"] = messages[1]["content"]
        return 200, {
            "model": "glm-5.3-flash",
            "choices": [{"message": {"content": "R"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    monkeypatch.setattr(llm, "post_chat", _post)
    report.summarize_transcript(make_transcript(), dict(META, suspected_silent=True))
    assert "转写疑似无语音" in captured["user"]
    assert "不要长篇复述" in captured["user"]


def test_new_run_dir_follows_config_runs_dir(monkeypatch, tmp_path):
    """new_run_dir 统一走 config.runs_dir()：环境变量覆盖即刻生效（不随 cwd 漂移）。"""
    monkeypatch.setenv("BILI_DIGEST_RUNS_DIR", str(tmp_path))
    run_dir = report.new_run_dir("BV1x")
    assert run_dir.is_relative_to(tmp_path / "BV1x")


def test_run_dir_roundtrip(tmp_path):
    tr = make_transcript()
    run_dir = report.new_run_dir("BV1test", base=tmp_path)
    assert run_dir.exists() and "BV1test" in str(run_dir)
    report.save_run(run_dir, {"transcript.txt": "第一句\n第二句",
                              "transcript.json": json.dumps(
                                  {"engine": "mlx-whisper", "source": "asr",
                                   "segments": [{"start": s.start, "end": s.end, "text": s.text}
                                                for s in tr.segments]})},
                    dict(META))
    text, meta = report.load_run(run_dir)
    assert "[00:00] 第0句内容" in text and "[01:30] 第3句内容" in text
    assert meta["title"] == "测试视频" and meta["engine"] == "mlx-whisper"

    path = report.save_report(run_dir, "# 报告", {"total_tokens": 15, "cost_yuan": 0.01})
    assert path.name == "report.md" and path.exists()
    saved = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert saved["summarize"]["cost_yuan"] == 0.01


def test_load_run_falls_back_to_txt(tmp_path):
    (tmp_path / "transcript.txt").write_text("只有纯文本", encoding="utf-8")
    text, meta = report.load_run(tmp_path)
    assert text == "只有纯文本" and meta == {}


def test_load_run_missing(tmp_path):
    with pytest.raises(CliError) as err:
        report.load_run(tmp_path)
    assert err.value.code == 2


# ---------------------------------------------------------------- 视觉增强注入（S6）

FRAMES = [
    {"id": "f00", "t": 8.0, "file": "frames/f00.jpg", "desc": "封面：经纬网基础", "has_text": True},
    {"id": "f01", "t": 61.0, "file": "frames/f01.jpg", "desc": "五带划分示意图", "has_text": True},
]


def test_visual_template_shipped():
    tpl = report.load_template("report-visual.md")
    assert "{title}" in tpl and "画面关键帧" in tpl
    assert "![mm:ss](frames/fNN.jpg)" in tpl


def test_build_messages_injects_frames():
    messages = report.build_messages("[00:00] 第一句", dict(META, frames=FRAMES))
    user = messages[1]["content"]
    assert "# 画面帧描述" in user
    assert "[00:08] f00（画面，含文字）封面：经纬网基础" in user
    assert "[01:01] f01（画面，含文字）五带划分示意图" in user
    assert "画面关键帧" in user              # 用的是视觉版输出模板
    assert "本视频无语音" not in user         # 非静音视频不注明
    skill = messages[0]["content"]
    assert "frames/fNN.jpg" in skill          # 总纲含嵌帧契约


def test_build_messages_silent_video_frames_chain():
    """验收 4：suspected_silent + frames → 注明「内容来自画面识别」链路。"""
    messages = report.build_messages(
        "[00:00] 幻觉", dict(META, frames=FRAMES, suspected_silent=True))
    user = messages[1]["content"]
    assert "画面帧描述是主要内容来源" in user
    assert "本视频无语音，内容来自画面识别" in user
    assert "转写疑似无语音" in user          # 元信息同时注明转写不可信


def test_build_messages_no_frames_unchanged():
    """无 frames：不出现画面节、用原模板（纯语音视频不受影响）。"""
    messages = report.build_messages("[00:00] 第一句", META)
    user = messages[1]["content"]
    assert "画面帧描述" not in user and "画面关键帧" not in user


def test_format_meta_lists_vision():
    text = report.format_meta({"vision": {"model": "glm-5.3-flash", "frames": 12}})
    assert "glm-5.3-flash" in text and "12 帧" in text


def test_load_run_reinjects_frames_for_report(monkeypatch, tmp_path, with_key):
    """digest report <run目录> 补报告：meta.json 里的 frames 自动进 prompt（路径回放）。"""
    captured = {}

    def _post(provider, messages, temperature=0.3, timeout=180):
        captured["user"] = messages[1]["content"]
        return 200, {"model": "glm-5.3-flash",
                     "choices": [{"message": {"content": "R"}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr(llm, "post_chat", _post)
    run_dir = report.new_run_dir("BV1vis", base=tmp_path)
    report.save_run(run_dir, {
        "transcript.json": json.dumps({"engine": "subtitle", "source": "subtitle",
                                       "segments": [{"start": 0.0, "end": 5.0, "text": "第一句"}]}),
    }, dict(META, frames=FRAMES))
    text, meta = report.load_run(run_dir)
    report.summarize_lines(text, meta)
    assert "[00:08] f00（画面，含文字）" in captured["user"]


# ---------------------------------------------------------------- S7：网络错误退避重试

def _ok_response(content="# 报告"):
    class Resp:
        status = 200
        def read(self):
            import json as _j
            return _j.dumps({"choices": [{"message": {"content": content}}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                             "model": "m"}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return Resp()


def test_post_chat_retries_once_on_network_error(monkeypatch):
    """读超时 → 退避一次重试成功（不抛 CliError）。"""
    import urllib.error
    monkeypatch.setattr(llm, "RETRY_NETWORK_WAIT", 0.0)
    monkeypatch.setattr(llm.config, "get_key", lambda name: "sk-test")
    calls = {"n": 0}

    def fake_urlopen(req, timeout=180):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("The read operation timed out")
        return _ok_response()
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    status, body = llm.post_chat("zhipu", [{"role": "user", "content": "hi"}])
    assert status == 200 and calls["n"] == 2


def test_post_chat_double_network_error_raises(monkeypatch):
    import urllib.error
    monkeypatch.setattr(llm, "RETRY_NETWORK_WAIT", 0.0)
    monkeypatch.setattr(llm.config, "get_key", lambda name: "sk-test")

    def always_timeout(req, timeout=180):
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(llm.urllib.request, "urlopen", always_timeout)
    with pytest.raises(CliError) as err:
        llm.post_chat("zhipu", [{"role": "user", "content": "hi"}])
    assert err.value.code == 4 and "网络" in str(err.value)


# ---------------------------------------------------------------- S7.5：撤销自动兜底（宿主接管协议）

def _ok_body(content="# 报告"):
    return {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "model": "m"}


def test_chat_402_no_auto_fallback(monkeypatch):
    """S7.5：402（余额不足）不再自动切供应商，直接抛错（由调用方落 report_error/
    宿主接管）；即使备用 key 已配置也不切。"""
    monkeypatch.setattr(llm.config, "get_key",
                        lambda n: "k" if n in ("DEEPSEEK_API_KEY", "ZHIPU_API_KEY") else None)
    calls = []

    def fake_post(provider, messages, temperature=0.3, timeout=180):
        calls.append(provider)
        return 402, {"error": {"message": "余额不足"}}
    monkeypatch.setattr(llm, "post_chat", fake_post)
    with pytest.raises(CliError) as err:
        llm.chat("siliconflow", [{"role": "user", "content": "hi"}])
    assert calls == ["siliconflow"]  # 不切家
    assert "余额不足" in str(err.value) or "充值" in str(err.value)


def test_chat_manual_provider_still_works(monkeypatch):
    """手动指定 provider（--provider/SUMMARIZE_PROVIDER 解析结果）照常直连。"""
    monkeypatch.setattr(llm.config, "get_key",
                        lambda n: "k" if n == "DEEPSEEK_API_KEY" else None)
    calls = []

    def fake_post(provider, messages, temperature=0.3, timeout=180):
        calls.append(provider)
        return 200, _ok_body("DeepSeek 手动直连")
    monkeypatch.setattr(llm, "post_chat", fake_post)
    content, usage = llm.chat("deepseek", [{"role": "user", "content": "hi"}])
    assert "手动直连" in content
    assert calls == ["deepseek"] and usage["provider"] == "deepseek"
    assert "fallback_chain" not in usage
