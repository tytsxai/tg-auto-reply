#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${1:-$ROOT_DIR/backups}"
DB_PATH="$ROOT_DIR/data/bot.db"
DEFAULT_KEY_PATH="$ROOT_DIR/data/encryption.key"

mkdir -p "$BACKUP_DIR"
TS="$(date +%F)"

if [ -f "$DB_PATH" ]; then
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/bot.db.$TS'"
    echo "✅ 已使用 sqlite3 备份数据库到 $BACKUP_DIR/bot.db.$TS"
  else
    cp "$DB_PATH" "$BACKUP_DIR/bot.db.$TS"
    echo "✅ 已复制数据库到 $BACKUP_DIR/bot.db.$TS"
  fi
else
  echo "⚠️ 未找到数据库文件：$DB_PATH" >&2
fi

if [ -z "${ENCRYPTION_KEY:-}" ]; then
  KEY_PATH="${ENCRYPTION_KEY_FILE:-$DEFAULT_KEY_PATH}"
  if [ -f "$KEY_PATH" ]; then
    cp "$KEY_PATH" "$BACKUP_DIR/encryption.key.$TS"
    echo "✅ 已备份密钥文件到 $BACKUP_DIR/encryption.key.$TS"
  else
    echo "⚠️ 未找到密钥文件：$KEY_PATH" >&2
  fi
else
  echo "ℹ️ 使用环境变量 ENCRYPTION_KEY，跳过密钥文件备份"
fi
