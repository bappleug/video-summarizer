#!/usr/bin/env python3
"""com.raymin.cookie_exporter — cookie-exporter 的 Native Messaging 本机助手。

协议：Chrome Native Messaging（stdin 读 4 字节小端长度前缀 + UTF-8 JSON，
stdout 以同格式回包；任何日志只能走 stderr，且不得包含 cookie 值）。

只认一个指令 write_key：把 {target}={value} 整行写入固定的 keys.env。
安全边界（详见 README）：
  - target 必须在白名单内（防止向任意字段注入）；
  - 写入路径写死为 ~/.config/ai-tools/bili-digest/keys.env，不接受消息指定路径
    （COOKIE_EXPORTER_KEYS_ENV 环境变量仅供单测注入，Chrome 正常启动时不存在）；
  - value 含换行直接拒绝（防止注入多行）；
  - 文件保持 0600 权限。
"""

import json
import os
import struct
import sys

# 固定写入路径；env 覆盖仅用于 tests/test_host.py 的子进程注入。
KEYS_ENV = os.environ.get(
    "COOKIE_EXPORTER_KEYS_ENV",
    os.path.expanduser("~/.config/ai-tools/bili-digest/keys.env"),
)
ALLOWED_TARGETS = {"XHS_COOKIE", "BILI_COOKIE"}


def read_message():
    """读一条 Native Messaging 消息；stdin 关闭/长度非法时返回 None。"""
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        return None
    (length,) = struct.unpack("<I", raw_len)
    if length == 0 or length > 10 * 1024 * 1024:
        print("native host: 消息长度非法", file=sys.stderr)
        return None
    data = sys.stdin.buffer.read(length)
    if len(data) < length:
        return None
    return json.loads(data.decode("utf-8"))


def write_message(obj):
    """按协议回包（唯一的 stdout 输出，不得打印其他内容）。"""
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def write_key(target, value):
    """写入/替换 keys.env 中的一行，返回是否为替换（而非新增）。"""
    line = f"{target}={value}"
    try:
        with open(KEYS_ENV, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    replaced = False
    for i, existing in enumerate(lines):
        if existing.startswith(f"{target}="):
            lines[i] = line
            replaced = True
            break
    if not replaced:
        lines.append(line)
    os.makedirs(os.path.dirname(KEYS_ENV), exist_ok=True)
    with open(KEYS_ENV, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(KEYS_ENV, 0o600)
    return replaced


def main():
    message = read_message()
    if message is None:
        return
    cmd = message.get("cmd")
    if cmd != "write_key":
        write_message({"ok": False, "error": f"未知指令: {cmd!r}"})
        return
    target = message.get("target", "")
    value = message.get("value", "")
    if target not in ALLOWED_TARGETS:
        write_message({"ok": False, "error": f"target 不在白名单: {target!r}"})
        return
    if "\n" in value or "\r" in value:
        write_message({"ok": False, "error": "value 含换行，拒绝写入"})
        return
    try:
        replaced = write_key(target, value)
    except OSError as exc:
        # OSError 文本只含 errno 与路径，不含 value。
        write_message({"ok": False, "error": f"写入失败: {exc}"})
        return
    write_message({"ok": True, "replaced": replaced})


if __name__ == "__main__":
    main()
