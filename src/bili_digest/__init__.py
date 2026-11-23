"""bili-digest —— B站视频 transcript 提取（字幕优先，无字幕回退本地 ASR）。"""

from importlib import metadata

_FALLBACK_VERSION = "1.2.1"  # 与 pyproject.toml 的 version 同步；仅在未安装（仅 PYTHONPATH=src）时使用


def _version():
    try:
        return metadata.version("bili-digest")
    except metadata.PackageNotFoundError:
        return _FALLBACK_VERSION


__version__ = _version()
