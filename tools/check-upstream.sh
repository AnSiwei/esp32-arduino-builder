#!/usr/bin/env bash
# 检查上游 arduino-esp32 是否有更新，返回 0=有更新 1=无更新
# 用法: ./tools/check-upstream.sh [arduino_repo_url]
set -euo pipefail

ARDUINO_REPO="${1:-https://github.com/espressif/arduino-esp32.git}"
STATE_FILE=".last_build_commit"

# 获取上游最新 commit
LATEST=$(git ls-remote "$ARDUINO_REPO" HEAD | cut -f1)
echo "上游最新 commit: $LATEST"

# 读取上次构建的 commit
if [ -f "$STATE_FILE" ]; then
  LAST=$(cat "$STATE_FILE")
else
  LAST=""
fi
echo "上次构建 commit: ${LAST:-<无记录>}"

if [ "$LATEST" = "$LAST" ]; then
  echo "上游无更新，跳过构建"
  exit 1
fi

echo "上游有更新，需要构建"
echo "$LATEST" > "$STATE_FILE"
exit 0