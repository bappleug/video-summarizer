#!/usr/bin/env bash
# uninstall-host.sh — 移除 Native Messaging 本机助手注册（macOS）。
# 只删 Chrome 里的注册清单，不动仓库内文件与 keys.env。
set -euo pipefail

DEST="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.raymin.cookie_exporter.json"

if [ -f "$DEST" ]; then
  rm "$DEST"
  echo "已移除 $DEST"
else
  echo "未发现 $DEST，无需处理"
fi
echo "请完全退出 Chrome（Cmd+Q）后重新打开使变更生效。"
