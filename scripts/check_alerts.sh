#!/bin/bash
set -euo pipefail

LOG_FILE="${LOG_FILE:-}"
if [ -z "$LOG_FILE" ]; then
  echo "❌ LOG_FILE 未设置"
  exit 2
fi
if [ ! -f "$LOG_FILE" ]; then
  echo "❌ 日志文件不存在：$LOG_FILE"
  exit 2
fi

LOOKBACK_LINES="${ALERT_LOOKBACK_LINES:-500}"
PATTERN="${ALERT_KEYWORDS:-AI 回复失败|回复队列已满|database is locked|登录已失效}"

if tail -n "$LOOKBACK_LINES" "$LOG_FILE" | grep -E "$PATTERN" >/dev/null 2>&1; then
  echo "🚨 告警：检测到关键错误日志"
  exit 1
fi

echo "✅ 告警检查通过"
