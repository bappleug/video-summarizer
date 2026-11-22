"""platforms/xiaohongshu 单测：链接解析 / state 解析 / 笔记与字幕 / 管线接入（不触网）。"""

import json
import urllib.parse
from pathlib import Path

import pytest

from bili_digest import pipeline
from bili_digest.errors import CliError
from bili_digest.platforms import xiaohongshu

NOTE_ID = "6411cf99000000001300b6d9"


def http_resp(status=200, body=b"", url="https://www.xiaohongshu.com/explore/x"):
    return (status, body, url)


def make_page(note_js, note_id=NOTE_ID):
    """真实页面里 state 含 undefined（loading 态、草稿等字段），一并覆盖 _js_to_json。"""
    state = (
        '{"note":{"currentNoteId":"%s","noteDetailMap":{"%s":{"note":%s,"loading":false}},'
        '"editor":{"draft":undefined}}}' % (note_id, note_id, note_js)
    )
    return "<html><head><script>window.__INITIAL_STATE__=" + state + "</script></head></html>"


def make_video_note(media_v2=None):
    note = {
        "type": "video",
        "title": "红烧肉做法教程",
        "desc": "家常菜",
        "user": {"nickname": "测试作者", "userId": "u1"},
        "video": {
            "capa": {"duration": 0},
            "media": {
                "video": {"duration": 10000},
                "stream": {
                    "h265": [{"duration": 9999, "masterUrl": "https://cdn/h265.mp4",
                              "backupUrls": ["https://cdn/h265-bk.mp4"]}],
                    "h264": [
                        {"duration": 10000, "masterUrl": "https://cdn/h264-1080.mp4",
                         "backupUrls": ["https://cdn/h264-bk.mp4"]},
                        {"duration": 5000, "masterUrl": "https://cdn/h264-720.mp4", "backupUrls": []},
                    ],
                },
            },
            "mediaV2": json.dumps(media_v2 or {}),
            "consumer": {"originVideoKey": "origin-key-should-be-unused"},
        },
    }
    return json.dumps(note, ensure_ascii=False)


SUBTITLE_V2 = {"video": {"subtitles": {
    "zh-CN": [{"url": "https://cdn/zh.srt", "language": "zh-CN"},
              {"url": "https://cdn/zh.vtt", "language": "zh-CN"}],
    "en": [{"url": "https://cdn/en.srt", "language": "en"}],
}}}


# ---------------------------------------------------------------- 链接解析

def test_resolve_long_url_keeps_xsec_token():
    note_id, url = xiaohongshu.resolve_note_url(
        "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9"
        "?xsec_token=ABcd123%3D&xsec_source=pc_feed&other=1")
    assert note_id == "6411cf99000000001300b6d9"
    assert url == ("https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9"
                   "?xsec_token=ABcd123%3D&xsec_source=pc_feed")


def test_resolve_discovery_item_path():
    note_id, url = xiaohongshu.resolve_note_url("http://xiaohongshu.com/discovery/item/6411cf99000000001300b6d9/")
    assert note_id == "6411cf99000000001300b6d9"
    assert url.startswith("https://www.xiaohongshu.com/explore/")


def test_resolve_share_text_with_single_link(monkeypatch):
    monkeypatch.setattr(
        xiaohongshu, "_short_probe",
        lambda url: (302, "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9"))
    note_id, _ = xiaohongshu.resolve_note_url("看看这个 https://xhslink.cn/aBc123/ 超好吃")
    assert note_id == "6411cf99000000001300b6d9"


def test_resolve_short_link_redirect_chain(monkeypatch):
    hops = {"https://xhslink.cn/aBc123": (302, "https://xhslink.cn/mid"),
            "https://xhslink.cn/mid": (301, "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9/?xsec_token=T1")}
    monkeypatch.setattr(xiaohongshu, "_short_probe", lambda url: hops[url])
    note_id, url = xiaohongshu.resolve_note_url("https://xhslink.cn/aBc123")
    assert note_id == "6411cf99000000001300b6d9"
    assert "xsec_token=T1" in url


def test_resolve_short_link_no_redirect(monkeypatch):
    monkeypatch.setattr(xiaohongshu, "_short_probe", lambda url: (200, None))
    with pytest.raises(CliError) as err:
        xiaohongshu.resolve_note_url("https://xhslink.cn/aBc123")
    assert err.value.code == 2


@pytest.mark.parametrize("value", [
    "https://www.youtube.com/watch?v=1",
    "https://www.xiaohongshu.com/user/profile/123",   # 非笔记路径
    "https://evil.com/xiaohongshu.com/explore/6411cf99000000001300b6d9",
    "https://user@xhslink.cn/aBc123",                 # netloc 带 userinfo
    "两个链接 https://xhslink.cn/a 和 https://xhslink.cn/b",
    "纯文本没有链接",
])
def test_resolve_rejects_invalid(value):
    with pytest.raises(CliError) as err:
        xiaohongshu.resolve_note_url(value)
    assert err.value.code == 2


# ---------------------------------------------------------------- state 解析

def test_js_to_json_transforms():
    out = xiaohongshu._js_to_json(
        "{a: 1, 'b': 'x\\'y', c: undefined, d: NaN, e: true, /* c */ f: [1, 2,], g: `中文`}")
    assert json.loads(out) == {"a": 1, "b": "x'y", "c": None, "d": None, "e": True,
                               "f": [1, 2], "g": "中文"}


def test_js_to_json_constructor_calls():
    """真实页面（2026-11 抓取）出现 "AiNoteDetailStore":{"noteDetailMap":new Map([])}。"""
    out = xiaohongshu._js_to_json(
        '{"a": new Map([]), "b": new Map ([["k1", 1], ["k2", "v"]]), '
        '"c": new Date("2026-11-21"), "d": new Foo(1, "x")}')
    assert json.loads(out) == {
        "a": {}, "b": {"k1": 1, "k2": "v"}, "c": "2026-11-21",
        "d": 'new Foo(1, "x")',
    }


def test_js_to_json_keeps_double_quoted_escape():
    out = xiaohongshu._js_to_json('{"k": "a\\"b\\\\c\\u4e2d"}')
    assert json.loads(out) == {"k": 'a"b\\c中'}


def test_parse_state_from_page():
    state = xiaohongshu.parse_state(make_page(make_video_note()))
    note = state["note"]["noteDetailMap"][NOTE_ID]["note"]
    assert note["title"] == "红烧肉做法教程"
    assert state["note"]["editor"]["draft"] is None


def test_parse_state_missing_marker():
    with pytest.raises(CliError) as err:
        xiaohongshu.parse_state("<html>验证页面</html>")
    assert err.value.code == 4


# ---------------------------------------------------------------- 笔记解析

def test_note_info_fields():
    page_note = json.loads(make_video_note(SUBTITLE_V2))
    info = xiaohongshu.note_info(page_note, NOTE_ID)
    assert info["note_id"] == NOTE_ID
    assert info["title"] == "红烧肉做法教程"
    assert info["owner"] == "测试作者"
    assert info["duration"] == 10.0            # media.video.duration 毫秒 → 秒
    assert info["video_urls"] == [              # h264 优先，masterUrl + backupUrls
        "https://cdn/h264-1080.mp4", "https://cdn/h264-bk.mp4",
        "https://cdn/h264-720.mp4",
        "https://cdn/h265.mp4", "https://cdn/h265-bk.mp4",
    ]
    assert info["subtitles"] == [
        {"language": "zh-CN", "url": "https://cdn/zh.srt"},   # 只收 .srt
        {"language": "en", "url": "https://cdn/en.srt"},
    ]


def test_note_info_duration_priority():
    note = json.loads(make_video_note())
    # stream 条目毫秒优先（max=10000ms → 10s），media.video.duration=10000 是秒也不采信
    assert xiaohongshu.note_info(note, NOTE_ID)["duration"] == 10.0
    # capa 秒兜底
    note["video"]["media"]["stream"] = {}
    note["video"]["capa"]["duration"] = 33.5
    assert xiaohongshu.note_info(note, NOTE_ID)["duration"] == 33.5
    # media.video.duration 秒兜底（实测该字段为秒；VRA 误判毫秒已修正）
    note["video"]["capa"]["duration"] = 0
    assert xiaohongshu.note_info(note, NOTE_ID)["duration"] == 10000
    # 全缺失 → None
    note["video"]["media"]["video"].pop("duration")
    assert xiaohongshu.note_info(note, NOTE_ID)["duration"] is None


def test_fetch_note_happy_path(monkeypatch):
    monkeypatch.setattr(xiaohongshu, "_http_get",
                        lambda url, cookie=None: http_resp(200, make_page(make_video_note(SUBTITLE_V2)).encode()))
    note = xiaohongshu._fetch_note(NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID)
    assert note["type"] == "video"
    assert xiaohongshu.note_info(note, NOTE_ID)["subtitles"][0]["url"] == "https://cdn/zh.srt"


def test_fetch_note_403_login_required(monkeypatch):
    monkeypatch.setattr(xiaohongshu, "_http_get", lambda url, cookie=None: http_resp(403, b""))
    with pytest.raises(xiaohongshu._LoginRequired):
        xiaohongshu._fetch_note(NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID)


def test_fetch_note_login_redirect(monkeypatch):
    monkeypatch.setattr(xiaohongshu, "_http_get",
                        lambda url, cookie=None: http_resp(200, b"", "https://www.xiaohongshu.com/login?next=/x"))
    with pytest.raises(xiaohongshu._LoginRequired):
        xiaohongshu._fetch_note(NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID)


def test_fetch_note_sec_404_shape(monkeypatch):
    """实测形态：200 → /404?error_code=300031&error_msg=当前笔记暂时无法浏览（state 为空壳）。"""
    final = ("https://www.xiaohongshu.com/404?source=/404/sec_x&error_code=300031&"
             "error_msg=" + urllib.parse.quote("当前笔记暂时无法浏览"))
    monkeypatch.setattr(xiaohongshu, "_http_get", lambda url, cookie=None: http_resp(200, b"<html>404</html>", final))
    with pytest.raises(xiaohongshu._LoginRequired) as err:
        xiaohongshu._fetch_note(NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID)
    assert "300031" in err.value.reason
    assert "当前笔记暂时无法浏览" in err.value.reason


def test_fetch_note_image_note_rejected(monkeypatch):
    page = make_page(json.dumps({"type": "normal", "title": "图文"}, ensure_ascii=False))
    monkeypatch.setattr(xiaohongshu, "_http_get", lambda url, cookie=None: http_resp(200, page.encode()))
    with pytest.raises(CliError) as err:
        xiaohongshu._fetch_note(NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID)
    assert err.value.code == 2


def test_fetch_note_missing_detail(monkeypatch):
    page = make_page(make_video_note(), note_id="ffffffffffffffffffffffff").encode()
    monkeypatch.setattr(xiaohongshu, "_http_get", lambda url, cookie=None: http_resp(200, page))
    with pytest.raises(CliError) as err:
        xiaohongshu._fetch_note(NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID)
    assert err.value.code == 3


def test_acquire_note_anonymous(monkeypatch):
    monkeypatch.setattr(xiaohongshu, "_fetch_note", lambda nid, url, cookie=None: json.loads(make_video_note()))
    monkeypatch.setattr(xiaohongshu, "resolve_note_url",
                        lambda v: (NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID))
    note_id, url, info, auth = xiaohongshu.acquire_note("https://xhslink.cn/aBc123")
    assert auth == "anonymous"


def test_acquire_note_blocked_without_cookie(monkeypatch):
    def blocked(nid, url, cookie=None):
        raise xiaohongshu._LoginRequired()
    monkeypatch.setattr(xiaohongshu, "_fetch_note", blocked)
    monkeypatch.setattr(xiaohongshu, "resolve_note_url",
                        lambda v: (NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID))
    monkeypatch.setattr(xiaohongshu.config, "get_key", lambda name: None)
    with pytest.raises(CliError) as err:
        xiaohongshu.acquire_note("https://xhslink.cn/aBc123")
    assert err.value.code == 4
    assert "XHS_COOKIE" in str(err.value)


def test_acquire_note_cookie_retry(monkeypatch):
    calls = []

    def flaky(nid, url, cookie=None):
        calls.append(cookie)
        if cookie is None:
            raise xiaohongshu._LoginRequired()
        return json.loads(make_video_note())

    monkeypatch.setattr(xiaohongshu, "_fetch_note", flaky)
    monkeypatch.setattr(xiaohongshu, "resolve_note_url",
                        lambda v: (NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID))
    monkeypatch.setattr(xiaohongshu.config, "get_key", lambda name: "a=1; b=2")
    note_id, url, info, auth = xiaohongshu.acquire_note("https://xhslink.cn/aBc123")
    assert auth == "cookie"
    assert calls == [None, "a=1; b=2"]


def test_acquire_note_cookie_rejected(monkeypatch):
    def blocked(nid, url, cookie=None):
        raise xiaohongshu._LoginRequired()
    monkeypatch.setattr(xiaohongshu, "_fetch_note", blocked)
    monkeypatch.setattr(xiaohongshu, "resolve_note_url",
                        lambda v: (NOTE_ID, "https://www.xiaohongshu.com/explore/" + NOTE_ID))
    monkeypatch.setattr(xiaohongshu.config, "get_key", lambda name: "a=1")
    with pytest.raises(CliError) as err:
        xiaohongshu.acquire_note("https://xhslink.cn/aBc123")
    assert err.value.code == 4
    assert "未接受当前登录态" in str(err.value)


# ---------------------------------------------------------------- 字幕

def test_pick_subtitle_prefers_zh():
    subs = [{"language": "en", "url": "u1"}, {"language": "zh-CN", "url": "u2"}]
    assert xiaohongshu.pick_subtitle(subs)["url"] == "u2"
    assert xiaohongshu.pick_subtitle([]) is None


def test_parse_srt():
    text = "1\n00:00:01,000 --> 00:00:03,500\n第一行\n第二行\n\n2\n00:00:04,000 --> 00:00:06,000\n第三行\n"
    segs = xiaohongshu.parse_srt(text)
    assert segs == [
        {"from": 1.0, "to": 3.5, "text": "第一行 第二行"},
        {"from": 4.0, "to": 6.0, "text": "第三行"},
    ]


def test_download_srt(monkeypatch):
    monkeypatch.setattr(xiaohongshu, "_http_get",
                        lambda url, cookie=None: http_resp(200, "1\n00:00:00,000 --> 00:00:02,000\n你好\n".encode()))
    segs = xiaohongshu.download_srt("https://cdn/zh.srt")
    assert segs == [{"from": 0.0, "to": 2.0, "text": "你好"}]


def test_download_srt_http_error(monkeypatch):
    monkeypatch.setattr(xiaohongshu, "_http_get", lambda url, cookie=None: http_resp(403, b""))
    with pytest.raises(CliError) as err:
        xiaohongshu.download_srt("https://cdn/zh.srt")
    assert err.value.code == 4


# ---------------------------------------------------------------- 视频流下载

def test_fetch_audio_mp4(monkeypatch, tmp_path):
    converted = []

    def fake_wav16k(src, wav):
        Path(wav).write_bytes(b"RIFF")
        converted.append(str(src))

    monkeypatch.setattr(xiaohongshu.audio, "to_wav16k", fake_wav16k)
    monkeypatch.setattr(xiaohongshu, "_http_get", lambda url, cookie=None: http_resp(200, b"fakemp4"))
    wav = xiaohongshu.fetch_audio(NOTE_ID, ["https://cdn/bad.mp4", "https://cdn/good.mp4"], tmp_path)
    assert wav.exists()
    assert converted[0].endswith(".mp4")   # 落盘文件，不是 URL


def test_fetch_audio_m3u8_direct_to_ffmpeg(monkeypatch, tmp_path):
    converted = []

    def fake_wav16k(src, wav):
        Path(wav).write_bytes(b"RIFF")
        converted.append(str(src))

    monkeypatch.setattr(xiaohongshu.audio, "to_wav16k", fake_wav16k)
    wav = xiaohongshu.fetch_audio(NOTE_ID, ["https://cdn/stream.m3u8"], tmp_path)
    assert converted == ["https://cdn/stream.m3u8"]  # URL 原样交给 ffmpeg


def test_fetch_audio_all_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(xiaohongshu, "_http_get", lambda url, cookie=None: http_resp(403, b""))
    with pytest.raises(CliError) as err:
        xiaohongshu.fetch_audio(NOTE_ID, ["https://cdn/a.mp4"], tmp_path)
    assert err.value.code == 4


def test_fetch_audio_no_urls(tmp_path):
    with pytest.raises(CliError) as err:
        xiaohongshu.fetch_audio(NOTE_ID, [], tmp_path)
    assert err.value.code == 3


# ---------------------------------------------------------------- 管线接入

SRT_SEGMENTS = [
    {"from": 0.0, "to": 5.0, "text": "红烧肉的家常做法"},
    {"from": 5.0, "to": 10.0, "text": "教程很简单很好吃"},
]


def fake_acquire(monkeypatch, subtitles, video_urls=("https://cdn/v.mp4",)):
    note_id = NOTE_ID
    url = "https://www.xiaohongshu.com/explore/" + NOTE_ID
    info = {"note_id": note_id, "title": "红烧肉做法教程", "desc": "家常菜",
            "owner": "测试作者", "duration": 10, "video_urls": list(video_urls),
            "subtitles": subtitles}
    monkeypatch.setattr(pipeline.xiaohongshu, "acquire_note",
                        lambda target: (note_id, url, info, "anonymous"))


class FakeEngine:
    name = "fake-asr"

    def transcribe(self, wav, lang="zh"):
        from bili_digest.transcribe.base import Segment, Transcript
        return Transcript([Segment(0.0, 4.0, "识别出的中文文本")],
                          lang=lang, engine=self.name, source="asr", elapsed_s=0.2)


def test_run_xhs_subtitle_path(monkeypatch):
    fake_acquire(monkeypatch, subtitles=[{"language": "zh-CN", "url": "https://cdn/zh.srt"}])
    monkeypatch.setattr(pipeline.xiaohongshu, "download_srt", lambda url: list(SRT_SEGMENTS))
    result = pipeline.run("https://xhslink.cn/aBc123")
    assert result.transcript.source == "subtitle"
    assert len(result.transcript.segments) == 2
    assert result.meta["platform"] == "xiaohongshu"
    assert result.meta["note_id"] == NOTE_ID
    assert result.meta["subtitle_lan"] == "zh-CN"


def test_run_xhs_subtitle_validate_fail_falls_to_asr(monkeypatch):
    fake_acquire(monkeypatch, subtitles=[{"language": "zh-CN", "url": "https://cdn/zh.srt"}])
    # 时长覆盖 40% < 60% → 校验不过 → 降级 ASR
    monkeypatch.setattr(pipeline.xiaohongshu, "download_srt",
                        lambda url: [dict(SRT_SEGMENTS[0])])
    monkeypatch.setattr(pipeline, "pick_engine", lambda name: FakeEngine())
    monkeypatch.setattr(pipeline.xiaohongshu, "fetch_audio",
                        lambda note_id, urls, wd: Path("/tmp/fake.wav"))
    result = pipeline.run("https://xhslink.cn/aBc123")
    assert result.transcript.source == "asr"
    assert result.meta["asr_engine"] == "fake-asr"
    assert result.meta["validate_warnings"]


def test_run_xhs_no_subtitle_asr(monkeypatch):
    fake_acquire(monkeypatch, subtitles=[])
    monkeypatch.setattr(pipeline, "pick_engine", lambda name: FakeEngine())
    monkeypatch.setattr(pipeline.xiaohongshu, "fetch_audio",
                        lambda note_id, urls, wd: Path("/tmp/fake.wav"))
    result = pipeline.run("https://www.xiaohongshu.com/explore/" + NOTE_ID)
    assert result.transcript.source == "asr"
    assert "validate_warnings" not in result.meta


def test_run_xhs_subtitle_blocked_raises(monkeypatch):
    fake_acquire(monkeypatch, subtitles=[{"language": "zh-CN", "url": "https://cdn/zh.srt"}])

    def blocked(url):
        raise CliError("字幕下载失败（HTTP 403）", 4)

    monkeypatch.setattr(pipeline.xiaohongshu, "download_srt", blocked)
    with pytest.raises(CliError) as err:
        pipeline.run("https://xhslink.cn/aBc123")
    assert err.value.code == 4


def test_run_dispatch(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "run_xiaohongshu",
                        lambda target, engine="auto", lang="zh", visual=False, work_dir=None, **kw:
                        seen.append("xhs"))
    monkeypatch.setattr(pipeline, "run_bilibili",
                        lambda target, page=None, engine="auto", lang="zh", visual=False, work_dir=None, **kw:
                        seen.append("bili"))
    pipeline.run("https://xhslink.cn/aBc123")
    pipeline.run("BV1CV411s7jd")
    pipeline.run("https://www.bilibili.com/video/BV1CV411s7jd")
    assert seen == ["xhs", "bili", "bili"]


def test_render_md_xhs_meta():
    from bili_digest.transcribe.base import Segment, Transcript
    transcript = Transcript([Segment(0.0, 2.0, "你好")], lang="zh", engine="subtitle",
                            source="subtitle", elapsed_s=1.0)
    meta = {"platform": "xiaohongshu", "note_id": NOTE_ID, "title": "红烧肉做法教程", "duration": 10}
    md = pipeline.render_md(transcript, meta)
    assert md.splitlines()[0] == "# 红烧肉做法教程"
    assert f"笔记: {NOTE_ID}" in md
    assert "BV:" not in md and "P:" not in md
