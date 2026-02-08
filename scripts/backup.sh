#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${1:-$ROOT_DIR/backups}"
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ROOT_DIR/.env"
  set +a
fi

DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./data/bot.db}"
db_path_from_url=""
is_memory_db=0
if [[ "$DATABASE_URL" == sqlite* ]]; then
  db_path_from_url="$DATABASE_URL"
  db_path_from_url="${db_path_from_url#sqlite+aiosqlite://}"
  db_path_from_url="${db_path_from_url#sqlite://}"
  if [[ "$db_path_from_url" == ":memory:" ]]; then
    is_memory_db=1
    db_path_from_url=""
  elif [[ "$db_path_from_url" == /./* ]]; then
    db_path_from_url="$ROOT_DIR/${db_path_from_url#/./}"
  elif [[ "$db_path_from_url" == //* ]]; then
    db_path_from_url="/${db_path_from_url##/}"
  elif [[ "$db_path_from_url" == /* ]]; then
    db_path_from_url="$db_path_from_url"
  else
    db_path_from_url="$ROOT_DIR/${db_path_from_url#./}"
  fi
fi

DB_PATH="$db_path_from_url"
if [[ -z "$DB_PATH" && "$DATABASE_URL" == sqlite* && "$is_memory_db" -eq 0 ]]; then
  DB_PATH="$ROOT_DIR/data/bot.db"
fi
DEFAULT_KEY_PATH="$ROOT_DIR/data/encryption.key"

mkdir -p "$BACKUP_DIR"
TS="$(date +%F)"

if [[ "$is_memory_db" -eq 1 ]]; then
  echo "ℹ️ DATABASE_URL=:memory:，跳过数据库文件备份"
elif [[ "$DATABASE_URL" != sqlite* ]]; then
  echo "ℹ️ 当前 DATABASE_URL 不是 SQLite，跳过数据库文件备份"
elif [ -f "$DB_PATH" ]; then
  if command -v sqlite3 >/dev/null 2>&1; then
    if sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/bot.db.$TS'"; then
      echo "✅ 已使用 sqlite3 备份数据库到 $BACKUP_DIR/bot.db.$TS"
    else
      cp "$DB_PATH" "$BACKUP_DIR/bot.db.$TS"
      echo "⚠️ sqlite3 备份失败，已复制数据库到 $BACKUP_DIR/bot.db.$TS"
    fi
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
