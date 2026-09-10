#!/usr/bin/env bash
# 交互式登录小红书并保存 cookies
# 用法:
#   ./login.sh                          # 自动检测浏览器
#   BIN_PATH=/path/to/chrome ./login.sh # 指定浏览器
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$SCRIPT_DIR/build/login"

if [[ ! -x "$BIN" ]]; then
    echo "错误: 找不到可执行文件 $BIN" >&2
    echo "请先编译: go build -o build/login ./cmd/login" >&2
    exit 1
fi

# 可通过环境变量覆盖
BIN_PATH="${BIN_PATH:-${ROD_BROWSER_BIN:-}}"

args=()
[[ -n "$BIN_PATH" ]] && args+=(-bin "$BIN_PATH")

exec "$BIN" "${args[@]+"${args[@]}"}"
