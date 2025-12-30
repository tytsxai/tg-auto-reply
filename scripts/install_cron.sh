#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${1:-$ROOT_DIR/backups}"
RETENTION_DAYS="${2:-${LOG_RETENTION_DAYS:-90}}"

CRON_BACKUP="0 3 * * * $ROOT_DIR/scripts/backup.sh $BACKUP_DIR"
CRON_CLEANUP="0 4 * * * $ROOT_DIR/scripts/cleanup_logs.py $RETENTION_DAYS"
CRON_ALERT="*/5 * * * * $ROOT_DIR/scripts/check_alerts.sh"

apply="${3:-}"

if [ "$apply" != "--apply" ]; then
  echo "将添加以下 cron 任务："
  echo "$CRON_BACKUP"
  echo "$CRON_CLEANUP"
  echo "$CRON_ALERT"
  echo ""
  echo "执行以下命令以应用："
  echo "./scripts/install_cron.sh $BACKUP_DIR $RETENTION_DAYS --apply"
  exit 0
fi

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

crontab -l 2>/dev/null > "$tmp_file" || true

add_line() {
  local line="$1"
  if ! grep -Fq "$line" "$tmp_file"; then
    echo "$line" >> "$tmp_file"
  fi
}

add_line "$CRON_BACKUP"
add_line "$CRON_CLEANUP"
add_line "$CRON_ALERT"

crontab "$tmp_file"
echo "✅ cron 任务已更新"
