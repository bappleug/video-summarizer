"""audio 单测：音轨选择纯函数 + ffmpeg 转码（真实子进程，tmp 目录）。"""

import subprocess

import pytest

from bili_digest import audio
from bili_digest.errors import CliError


def test_pick_audio_track_max_bandwidth():
    tracks = [
        {"id": 30216, "bandwidth": 67463},
        {"id": 30280, "bandwidth": 292592},
        {"id": 30232, "bandwidth": 111843},
    ]
    assert audio.pick_audio_track(tracks)["id"] == 30280


def test_pick_audio_track_missing_bandwidth():
    tracks = [{"id": 30216}, {"id": 30280, "bandwidth": 100}]
    assert audio.pick_audio_track(tracks)["id"] == 30280


def test_pick_audio_track_empty():
    with pytest.raises(CliError) as err:
        audio.pick_audio_track([])
    assert err.value.code == 3


def make_sine_m4a(path):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=r=44100:duration=1", "-c:a", "aac", str(path)],
        check=True,
    )


def test_to_wav16k_roundtrip(tmp_path):
    m4a = tmp_path / "tone.m4a"
    wav = tmp_path / "tone.wav"
    make_sine_m4a(m4a)
    audio.to_wav16k(m4a, wav)
    assert wav.exists()
    assert wav.stat().st_size > 10000  # 1s * 16kHz * 2B ≈ 32KB


def test_to_wav16k_rejects_garbage(tmp_path):
    m4a = tmp_path / "fake.m4a"
    wav = tmp_path / "out.wav"
    m4a.write_bytes(b"this is not audio at all")
    with pytest.raises(CliError) as err:
        audio.to_wav16k(m4a, wav)
    assert err.value.code == 1
