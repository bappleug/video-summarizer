"""版本号单测：__version__ 与 pyproject.toml 保持一致（MCP 握手向客户端报告的就是它）。

背景：__version__ 曾写死 "0.1.0" 而 pyproject 已到 1.2.x，MCP 客户端里显示的版本一直是错的。
"""

import tomllib
from importlib import metadata
from pathlib import Path

import pytest

import bili_digest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pyproject_version():
    with open(PYPROJECT, "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _fallback():
    return bili_digest._FALLBACK_VERSION


def test_version_matches_pyproject():
    """对外报告的版本 == pyproject 的版本（已安装时读元数据，未安装时读兜底字面量）。"""
    assert bili_digest.__version__ == _pyproject_version()


def test_version_fallback_matches_pyproject():
    """兜底字面量与 pyproject 同步：无 editable install 的部署机（仅 PYTHONPATH=src）也报告正确版本。"""
    assert _fallback() == _pyproject_version()


def test_version_without_installed_metadata(monkeypatch):
    """模拟未安装（PackageNotFoundError）：回退到兜底字面量而不是抛异常。"""
    def not_found(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(bili_digest.metadata, "version", not_found)
    assert bili_digest._version() == _pyproject_version()


@pytest.mark.skipif(
    not any("bili_digest" in (d.metadata["Name"] or "").replace("-", "_")
            for d in metadata.distributions()),
    reason="本机未安装 bili-digest（仅 PYTHONPATH=src 运行）",
)
def test_installed_metadata_not_stale():
    """已安装时：安装元数据里的版本不能落后于 pyproject——editable 安装快照过期会让握手报旧版本。"""
    assert metadata.version("bili-digest") == _pyproject_version()
