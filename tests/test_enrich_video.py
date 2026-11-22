"""enrich.video 单测：DASH 视频轨选择 / B站与小红书下载重试（mock HTTP，不触网）。"""

import pytest

from bili_digest import enrich
from bili_digest.errors import CliError


def test_pick_video_track_prefers_720p_h264():
    """S6.3 选流：≤720p 内优先 h264 再取最大带宽（读图足够，长视频体积控制）。"""
    tracks = [
        {"id": 16, "bandwidth": 200000, "codecs": "avc1", "height": 360},
        {"id": 80, "bandwidth": 2000000, "codecs": "hev1", "height": 1080},  # 带宽最高但超上限
        {"id": 64, "bandwidth": 900000, "codecs": "hev1", "height": 720},    # ≤720p 但 hevc
        {"id": 32, "bandwidth": 800000, "codecs": "avc1", "height": 480},   # ≤720p h264
    ]
    assert enrich.video.pick_video_track(tracks)["id"] == 32


def test_pick_video_track_falls_back_when_all_above_cap():
    """全部轨道超 720p → 退回全列表最大带宽（有总比没有好）。"""
    tracks = [
        {"id": 112, "bandwidth": 1000000, "codecs": "hevc", "height": 2160},
        {"id": 80, "bandwidth": 2000000, "codecs": "hev1", "height": 1080},
    ]
    assert enrich.video.pick_video_track(tracks)["id"] == 80


def test_pick_video_track_empty():
    with pytest.raises(CliError) as err:
        enrich.video.pick_video_track([])
    assert err.value.code == 3 and "没有 DASH 视频轨" in str(err.value)


def test_fetch_bilibili_video_downloads_and_falls_back(monkeypatch, tmp_path):
    calls = {"playurl": 0, "cdn": []}

    def fake_api_json(url, cookie=None, form=None, referer=None):
        calls["playurl"] += 1
        return {"code": 0, "data": {"dash": {"video": [
            {"id": 32, "bandwidth": 800000, "codecs": "avc1", "width": 854, "height": 480,
             "baseUrl": "https://cdn1/v32.m4s", "backupUrl": ["https://cdn2/v32.m4s"]},
            {"id": 80, "bandwidth": 2000000, "codecs": "avc1", "width": 1920, "height": 1080,
             "baseUrl": "https://cdn1/v80.m4s", "backupUrl": []},
        ]}}}

    def fake_http(url, cookie=None, form=None, referer=None):
        calls["cdn"].append(url)
        if url == "https://cdn1/v32.m4s":
            return 403, b"", url  # 首选 CDN 被拒 → 换 backup
        return 200, b"MP4DATA", url

    monkeypatch.setattr(enrich.video.bilibili, "api_json", fake_api_json)
    monkeypatch.setattr(enrich.video.bilibili, "http", fake_http)
    info = {}
    out = enrich.video.fetch_bilibili_video("BV1x", 100, tmp_path, info=info)
    assert out == tmp_path / "BV1x_100.mp4"
    assert out.read_bytes() == b"MP4DATA"
    assert calls["playurl"] == 1
    assert calls["cdn"] == ["https://cdn1/v32.m4s", "https://cdn2/v32.m4s"]  # ≤720p h264 轨 + 备选
    assert info["stream_id"] == 32 and info["codec"] == "avc1"
    assert info["resolution"] == "854x480" and "video_mb" in info


def test_fetch_bilibili_video_no_dash(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich.video.bilibili, "api_json",
                        lambda url, cookie=None, form=None, referer=None: {"code": 0, "data": {}})
    with pytest.raises(CliError) as err:
        enrich.video.fetch_bilibili_video("BV1x", 100, tmp_path)
    assert err.value.code == 3


def test_fetch_xhs_video_mp4_and_cleanup_between_failures(monkeypatch, tmp_path):
    state = {"n": 0}

    def fake_get(url):
        state["n"] += 1
        if state["n"] == 1:
            return 403, b"", url  # 第一个地址被拒
        return 200, b"XHSMP4", url

    monkeypatch.setattr(enrich.video.xiaohongshu, "_http_get", fake_get)
    out = enrich.video.fetch_xhs_video("note1", ["https://a/x.mp4", "https://b/y.mp4"], tmp_path)
    assert out.read_bytes() == b"XHSMP4"
    assert state["n"] == 2


def test_fetch_xhs_video_all_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich.video.xiaohongshu, "_http_get",
                        lambda url: (403, b"", url))
    with pytest.raises(CliError) as err:
        enrich.video.fetch_xhs_video("note1", ["https://a/x.mp4"], tmp_path)
    assert err.value.code == 4


def test_fetch_xhs_video_empty_urls(tmp_path):
    with pytest.raises(CliError) as err:
        enrich.video.fetch_xhs_video("note1", [], tmp_path)
    assert err.value.code == 3


def test_download_dir_created(tmp_path, monkeypatch):
    """work_dir 不存在时自动建（download/ 子目录约定）。"""
    monkeypatch.setattr(enrich.video.xiaohongshu, "_http_get",
                        lambda url: (200, b"XHSMP4", url))
    out = enrich.video.fetch_xhs_video("note1", ["https://a/x.mp4"], tmp_path / "download")
    assert out.parent == tmp_path / "download" and out.exists()
