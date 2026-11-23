#!/usr/bin/env python3
"""test_host.py — Native Messaging host 的子进程全链路单测（unittest，零第三方依赖）。

以真实协议帧（4 字节小端长度前缀 + JSON）通过 stdin/stdout 管道驱动 native/host.py，
并用 COOKIE_EXPORTER_KEYS_ENV 环境变量把写入路径指到临时目录，绝不触碰真实 keys.env。
"""

import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOST_PY = Path(__file__).resolve().parent.parent / "native" / "host.py"


def frame(message):
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(data)) + data


def unframe(raw):
    assert len(raw) >= 4, "回包缺长度前缀"
    (length,) = struct.unpack("<I", raw[:4])
    assert len(raw) == 4 + length, "回包长度前缀与实际内容不符"
    return json.loads(raw[4:].decode("utf-8"))


class HostPipelineTest(unittest.TestCase):
    """每个用例独立临时目录 + 独立 env，互不污染。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.keys_env = self.tmp / "keys.env"
        self.env = dict(os.environ, COOKIE_EXPORTER_KEYS_ENV=str(self.keys_env))

    def call(self, message):
        proc = subprocess.run(
            [sys.executable, str(HOST_PY)],
            input=frame(message),
            capture_output=True,
            timeout=10,
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, f"host 异常退出: {proc.stderr.decode()}")
        reply = unframe(proc.stdout)
        # 协议纪律：stdout 只能有这一个回包，任何日志只能走 stderr。
        self.assertNotIn(b"value", proc.stderr)
        return reply

    def read_file(self):
        return self.keys_env.read_text(encoding="utf-8")

    # ---- 写入语义 ----

    def test_新文件追加整行并保持0600(self):
        reply = self.call({"cmd": "write_key", "target": "XHS_COOKIE", "value": "a1=fake; web_session=fake"})
        self.assertEqual(reply, {"ok": True, "replaced": False})
        self.assertEqual(self.read_file(), "XHS_COOKIE=a1=fake; web_session=fake\n")
        mode = stat.S_IMODE(self.keys_env.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_已存在字段整行替换其他行保持不动(self):
        self.keys_env.write_text(
            "LLM_API_KEY=keep\nXHS_COOKIE=stale\nOTHER=keep\n", encoding="utf-8"
        )
        reply = self.call({"cmd": "write_key", "target": "XHS_COOKIE", "value": "a1=new"})
        self.assertEqual(reply, {"ok": True, "replaced": True})
        self.assertEqual(self.read_file(), "LLM_API_KEY=keep\nXHS_COOKIE=a1=new\nOTHER=keep\n")

    def test_目标目录不存在时自动创建(self):
        self.keys_env = self.tmp / "nested" / "deep" / "keys.env"
        self.env["COOKIE_EXPORTER_KEYS_ENV"] = str(self.keys_env)
        reply = self.call({"cmd": "write_key", "target": "BILI_COOKIE", "value": "SESSDATA=fake"})
        self.assertTrue(reply["ok"])
        self.assertEqual(self.read_file(), "BILI_COOKIE=SESSDATA=fake\n")

    def test_两次写入同一字段只留最新一行(self):
        self.call({"cmd": "write_key", "target": "XHS_COOKIE", "value": "old"})
        reply = self.call({"cmd": "write_key", "target": "XHS_COOKIE", "value": "new"})
        self.assertTrue(reply["replaced"])
        self.assertEqual(self.read_file(), "XHS_COOKIE=new\n")

    # ---- 安全边界 ----

    def test_白名单外的target被拒绝且文件不变(self):
        self.keys_env.write_text("XHS_COOKIE=keep\n", encoding="utf-8")
        reply = self.call({"cmd": "write_key", "target": "EVIL_KEY", "value": "x"})
        self.assertFalse(reply["ok"])
        self.assertIn("白名单", reply["error"])
        self.assertEqual(self.read_file(), "XHS_COOKIE=keep\n")

    def test_value含换行被拒绝(self):
        reply = self.call({"cmd": "write_key", "target": "XHS_COOKIE", "value": "a=1\nEVIL=2"})
        self.assertFalse(reply["ok"])
        self.assertFalse(self.keys_env.exists())

    def test_未知指令被拒绝(self):
        reply = self.call({"cmd": "read_any_file", "path": "/etc/passwd"})
        self.assertFalse(reply["ok"])
        self.assertFalse(self.keys_env.exists())

    # ---- 协议 ----

    def test_stdin提前关闭不崩溃(self):
        proc = subprocess.run(
            [sys.executable, str(HOST_PY)], input=b"", capture_output=True, timeout=10, env=self.env
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")


if __name__ == "__main__":
    unittest.main()
