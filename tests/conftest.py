"""套件级守卫：测试不碰开发机真实的凭据与网络（测试一律 mock）。

- 配置目录：包按 XDG_CONFIG_HOME 定位 keys.env / credentials.json，这里在导入被测包之前
  把它指到临时目录——测试进程及其拉起的子进程都读不到真实凭据文件。
- 网络：漏 mock 的用例会在第一次 connect 时直接报错，而不是悄悄打到真实接口。
"""

import atexit
import os
import shutil
import socket
import tempfile

import pytest

_TEST_XDG = tempfile.mkdtemp(prefix="bili-digest-tests-")
os.environ["XDG_CONFIG_HOME"] = _TEST_XDG
atexit.register(shutil.rmtree, _TEST_XDG, ignore_errors=True)


def _blocked(*args, **kwargs):
    raise RuntimeError("测试内禁止真实网络连接，请 mock")


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
