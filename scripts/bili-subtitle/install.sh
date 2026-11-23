#!/bin/sh
set -eu

TOOL_DIR=$(cd "$(dirname "$0")" && pwd)
TARGET_DIR="${BILI_BIN_DIR:-$HOME/.local/bin}"
LINK="$TARGET_DIR/bili"

mkdir -p "$TARGET_DIR"
chmod +x "$TOOL_DIR/bin/bili"

if [ -L "$LINK" ] || [ -e "$LINK" ]; then
  echo "已存在 ${LINK}，先删除再重装"
  rm -f "$LINK"
fi

ln -s "$TOOL_DIR/bin/bili" "$LINK"
echo "已安装: $LINK -> $TOOL_DIR/bin/bili"

case ":$PATH:" in
  *":$TARGET_DIR:"*) ;;
  *) echo "注意: $TARGET_DIR 不在 PATH 里，需要把它加进去" ;;
esac

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || { echo "需要 Python 3.8+"; exit 1; }
command -v openssl >/dev/null || echo "注意: 没有 openssl，自动续期不可用（登录和抓字幕不受影响）"
command -v qrencode >/dev/null || echo "提示: 没装 qrencode，登录时终端画不出二维码（brew install qrencode；也可以用 bili login --open 或 --url）"

"$LINK" --help >/dev/null && echo "自检通过: bili --help"
