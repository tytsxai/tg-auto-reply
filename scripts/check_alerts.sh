#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ROOT_DIR/.env"
  set +a
fi

LOG_FILE="${LOG_FILE:-}"
if [ -z "$LOG_FILE" ]; then
  # LOG_FILE 未配置时静默跳过，避免 cron 误报
  exit 0
fi
if [ ! -f "$LOG_FILE" ]; then
  # 日志文件尚不存在（服务未启动或首次运行），静默跳过
  exit 0
fi

LOOKBACK_LINES="${ALERT_LOOKBACK_LINES:-500}"
PATTERN="${ALERT_KEYWORDS:-AI 回复失败|回复队列已满|database is locked|登录已失效|terminated by other getUpdates}"

if tail -n "$LOOKBACK_LINES" "$LOG_FILE" | grep -E "$PATTERN" >/dev/null 2>&1; then
  echo "🚨 告警：检测到关键错误日志"
  exit 1
fi

echo "✅ 告警检查通过"
