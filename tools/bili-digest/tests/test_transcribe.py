"""transcribe 组件单测：cloud_sf 错误分类 / multipart / 401 换端点重试（不触网）。

另含组件独立性验收：import bili_digest.transcribe 不得引入壳工程（platforms / pipeline）。
"""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from bili_digest import config
from bili_digest.errors import CliError
from bili_digest.transcribe import cloud_sf, mlx_backend
from bili_digest.transcribe.base import Segment, Transcript, suspects_silence


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setattr(config, "get_key", lambda name: "sk-test-key" if name == "SILICONFLOW_API_KEY" else None)


def ok_response(text="你好世界", seconds=300):
    return 200, {"text": text, "language": "Chinese", "usage": {"type": "duration", "seconds": seconds}}


def test_multipart_body_format():
    body, ctype = cloud_sf.build_multipart(
        {"model": cloud_sf.MODEL}, "a.wav", b"\x00\x01\x02", "audio/wav")
    assert ctype.startswith("multipart/form-data; boundary=----bili-digest-")
    assert b'name="model"\r\n\r\nFunAudioLLM/SenseVoiceSmall' in body
    assert b'filename="a.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert body.endswith(b"--\r\n") and body.startswith(b"------bili-digest-")  # 「--」+ boundary


def test_cloud_transcribe_ok(fake_key, tmp_path, monkeypatch):
    calls = []

    def fake_post(endpoint, key, path):
        calls.append(endpoint)
        return ok_response()

    monkeypatch.setattr(cloud_sf, "post_transcription", fake_post)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\x00" * 1024)
    tr = cloud_sf.CloudSfTranscriber().transcribe(wav)
    assert isinstance(tr, Transcript)
    assert tr.engine == "siliconflow-sensevoice"
    assert tr.source == "asr"
    assert tr.segments == [Segment(0.0, 300.0, "你好世界")]
    assert calls == [cloud_sf.ENDPOINTS[0]]  # 默认 .cn，一次成功


def test_cloud_401_retries_com_endpoint(fake_key, tmp_path, monkeypatch):
    calls = []

    def fake_post(endpoint, key, path):
        calls.append(endpoint)
        return ok_response() if endpoint.endswith(".com") else (401, {"message": "Token is invalid."})

    monkeypatch.setattr(cloud_sf, "post_transcription", fake_post)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\x00" * 1024)
    tr = cloud_sf.CloudSfTranscriber().transcribe(wav)
    assert tr.segments[0].text == "你好世界"
    assert calls == list(cloud_sf.ENDPOINTS)  # .cn 401 → 自动换 .com


@pytest.mark.parametrize("status,keywords,code", [
    (401, ["key 无效", "cloud.siliconflow.cn", "digest doctor"], 3),
    (402, ["余额不足", "充值"], 3),
    (403, ["实名"], 3),
    (429, ["限流"], 4),
    (500, ["云转写失败", "HTTP 500"], 4),
])
def test_cloud_error_classification(fake_key, tmp_path, monkeypatch, status, keywords, code):
    monkeypatch.setattr(cloud_sf, "post_transcription",
                        lambda endpoint, key, path: (status, {"message": "boom"}))
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\x00" * 1024)
    with pytest.raises(CliError) as err:
        cloud_sf.CloudSfTranscriber().transcribe(wav)
    assert err.value.code == code
    for word in keywords:
        assert word in str(err.value)


def test_cloud_no_key_guidance(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "get_key", lambda name: None)
    eng = cloud_sf.CloudSfTranscriber()
    assert eng.available() is False
    with pytest.raises(CliError) as err:
        eng.transcribe(tmp_path / "a.wav")
    assert err.value.code == 3
    assert "SILICONFLOW_API_KEY" in str(err.value)
    assert "digest doctor" in str(err.value)


def test_cloud_big_file_shrinks_to_mp3(fake_key, tmp_path, monkeypatch):
    uploaded = {}

    def fake_post(endpoint, key, path):
        uploaded["name"] = path.name
        return ok_response()

    monkeypatch.setattr(cloud_sf, "post_transcription", fake_post)
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"\x00" * 128)
    monkeypatch.setattr(cloud_sf, "shrink_to_mp3", lambda wav, wd: mp3)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\x00" * (cloud_sf.MAX_UPLOAD_BYTES + 1))
    tr = cloud_sf.CloudSfTranscriber().transcribe(wav)
    assert tr.segments[0].text == "你好世界"
    assert uploaded["name"] == "a.mp3"


def test_cloud_empty_text_is_error(fake_key, tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_sf, "post_transcription", lambda e, k, p: ok_response(text="  "))
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\x00" * 1024)
    with pytest.raises(CliError) as err:
        cloud_sf.CloudSfTranscriber().transcribe(wav)
    assert "空文本" in str(err.value)


def test_transcribe_component_is_independent():
    """验收 5：transcribe 组件脱离 pipeline 可独立 import（不连带壳工程模块）。"""
    code = (
        "import sys; import bili_digest.transcribe as t; "
        "assert hasattr(t, 'Transcriber') and hasattr(t, 'CloudSfTranscriber'); "
        "mods = [m for m in sys.modules if m.startswith('bili_digest.platforms') "
        "or m == 'bili_digest.pipeline' or m == 'bili_digest.cli']; "
        "print('LEAKED:' + ','.join(mods) if mods else 'INDEPENDENT')"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "INDEPENDENT"


# ---------------------------------------------------------------- 无语音检测（幻觉循环）

# 2026-11-21 实测纯 BGM 小红书笔记的 mlx-whisper 幻觉产物（同句署名文本 ×4，时间戳超出视频时长）
REAL_HALLUCINATION = Path(__file__).resolve().parents[1] / (
    "runs/6aa6179a000000002603143d/20261121-001830/transcript.json")


def raw_seg(text, start=0.0, end=5.0, **extra):
    return {"start": start, "end": end, "text": text, **extra}


def run_mlx_with_raw(monkeypatch, raw_segments):
    """注入假 mlx_whisper 模块（无需安装 mlx extra），用原始 segments dict 跑转写。"""
    fake = types.ModuleType("mlx_whisper")
    fake.transcribe = lambda audio, **kw: {"segments": raw_segments, "text": "unused"}
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    return mlx_backend.MlxTranscriber().transcribe(Path("a.wav"))


def test_silence_heuristic_repeated_run():
    """① 相邻段同文本连续重复 ≥3 段 → 命中（真实案例形态：同句 ×4）。"""
    segs = [Segment(i * 30.0, i * 30.0 + 30.0, "字幕志愿者 杨茜茜") for i in range(4)]
    assert suspects_silence(segs) is True


def test_silence_heuristic_two_repeats_not_flagged():
    segs = [Segment(0, 5, "你好"), Segment(5, 10, "你好"), Segment(10, 15, "世界")]
    assert suspects_silence(segs) is False


def test_silence_heuristic_low_unique_ratio():
    """② 无 ≥3 连重复但唯一文本占比 < 20% → 命中。"""
    segs = [Segment(i * 5.0, i * 5.0 + 5.0, "甲乙"[i % 2]) for i in range(11)]  # 2/11 ≈ 18%
    assert suspects_silence(segs) is True


def test_silence_heuristic_normal_text_not_flagged():
    segs = [Segment(i * 5.0, i * 5.0 + 5.0, f"第{i}句话") for i in range(5)]
    assert suspects_silence(segs) is False


def test_silence_heuristic_empty_segments():
    assert suspects_silence([]) is False


def test_mlx_drops_high_no_speech_segments(monkeypatch, capsys):
    """no_speech_prob > 0.6 的段丢弃（whisper 官方惯例），其余保留不误标。"""
    tr = run_mlx_with_raw(monkeypatch, [
        raw_seg("无语音段", no_speech_prob=0.9),
        raw_seg("正常段", no_speech_prob=0.1),
    ])
    assert [s.text for s in tr.segments] == ["正常段"]
    assert tr.suspected_silent is False
    assert "no_speech_prob" in capsys.readouterr().err


def test_mlx_marks_hallucination_loop(monkeypatch):
    tr = run_mlx_with_raw(monkeypatch, [
        raw_seg("字幕志愿者 杨茜茜", start=i * 30.0, end=i * 30.0 + 30.0) for i in range(4)
    ])
    assert tr.suspected_silent is True
    assert len(tr.segments) == 4  # 启发式只标记，segments 保留过滤后的


def test_real_hallucination_run_marked(monkeypatch):
    """真实幻觉数据（runs/ 下的 2026-11-21 产物）驱动：经 mlx 后端被标记 suspected_silent。"""
    if not REAL_HALLUCINATION.exists():
        pytest.skip("真实幻觉样本 runs/6aa6179a.../transcript.json 不在本机")
    data = json.loads(REAL_HALLUCINATION.read_text(encoding="utf-8"))
    assert suspects_silence([Segment(**s) for s in data["segments"]]) is True
    tr = run_mlx_with_raw(monkeypatch, data["segments"])
    assert tr.suspected_silent is True


def test_cloud_heuristic_only_no_prob_fields(fake_key, tmp_path, monkeypatch):
    """云 SenseVoice 只回整段文本（无概率字段）→ 只走启发式，正常文本不误报。"""
    monkeypatch.setattr(cloud_sf, "post_transcription", lambda e, k, p: ok_response())
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\x00" * 1024)
    tr = cloud_sf.CloudSfTranscriber().transcribe(wav)
    assert tr.suspected_silent is False


# ---------------------------------------------------------------- S7：语言自动检测

def test_mlx_lang_none_omits_language_and_uses_detected(monkeypatch):
    """lang=None：不传 language（mlx-whisper 自带检测），Transcript.lang 用检测结果。"""
    kwargs_seen = {}

    def fake_transcribe(audio, **kw):
        kwargs_seen.update(kw)
        return {"segments": [raw_seg("hello world")], "text": "x", "language": "en"}
    fake = types.ModuleType("mlx_whisper")
    fake.transcribe = fake_transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    tr = mlx_backend.MlxTranscriber().transcribe(Path("a.wav"))
    assert "language" not in kwargs_seen  # 自动检测：不传
    assert tr.lang == "en"


def test_mlx_explicit_lang_passed_through(monkeypatch):
    kwargs_seen = {}

    def fake_transcribe(audio, **kw):
        kwargs_seen.update(kw)
        return {"segments": [raw_seg("你好")], "text": "x", "language": "zh"}
    fake = types.ModuleType("mlx_whisper")
    fake.transcribe = fake_transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    tr = mlx_backend.MlxTranscriber().transcribe(Path("a.wav"), lang="zh")
    assert kwargs_seen.get("language") == "zh" and tr.lang == "zh"
