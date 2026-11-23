#!/usr/bin/env bash
# install-host.sh — 把 Native Messaging 本机助手注册到 Chrome（macOS）。
# 产出：~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.raymin.cookie_exporter.json
# 用法：bash cookie-exporter/native/install-host.sh
set -euo pipefail

# 与 cookie-exporter/manifest.json 的 key 字段（公钥）对应的稳定扩展 ID；配对的私钥由维护者在仓库外保管。
EXTENSION_ID="cilhmdbgnppkdmeonmdfnijpejfmfclm"

HERE="$(cd "$(dirname "$0")" && pwd)"
HOST_PY="$HERE/host.py"
TEMPLATE="$HERE/com.raymin.cookie_exporter.json"
DEST_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
DEST="$DEST_DIR/com.raymin.cookie_exporter.json"

chmod +x "$HOST_PY"

mkdir -p "$DEST_DIR"
sed -e "s|__EXTENSION_ID__|$EXTENSION_ID|g" \
    -e "s|__HOST_PATH__|$HOST_PY|g" \
    "$TEMPLATE" > "$DEST"
python3 -m json.tool "$DEST" > /dev/null  # 校验生成物是合法 JSON

echo "已写入 $DEST"
echo "host 脚本：${HOST_PY}（已 chmod +x）"
echo "允许的扩展：chrome-extension://$EXTENSION_ID/"
echo "请完全退出 Chrome（Cmd+Q）后重新打开，再使用「直写 keys.env」。"
