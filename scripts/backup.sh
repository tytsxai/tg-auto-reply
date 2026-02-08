#!/bin/bash
set -euo pipefail
umask 077

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
TS="$(date +%Y%m%d-%H%M%S)"

sqlite_hot_backup() {
  local src="$1"
  local dst="$2"
  if command -v sqlite3 >/dev/null 2>&1; then
    # sqlite3 某些场景下即使报错也可能返回 0，这里显式校验 quick_check。
    local source_check
    source_check="$(sqlite3 "$src" "PRAGMA quick_check;" 2>/dev/null || true)"
    if [[ "$source_check" == "ok" ]]; then
      if sqlite3 "$src" ".backup '$dst'" >/dev/null 2>&1; then
        return 0
      fi
    fi
    echo "⚠️ sqlite3 备份失败，尝试 Python 回退" >&2
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    return 1
  fi

  python3 - "$src" "$dst" <<'PY'
import sqlite3
import sys

source_path, target_path = sys.argv[1], sys.argv[2]
source = sqlite3.connect(source_path, timeout=30)
target = sqlite3.connect(target_path)
try:
    with target:
        source.backup(target)
finally:
    target.close()
    source.close()
PY
  return $?
}

sqlite_validate_backup() {
  local db="$1"
  if ! command -v python3 >/dev/null 2>&1; then
    return 1
  fi
  python3 - "$db" <<'PY'
import sqlite3
import sys

path = sys.argv[1]
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    row = conn.execute("PRAGMA quick_check").fetchone()
    if not row or str(row[0]).lower() != "ok":
        raise SystemExit(1)
finally:
    conn.close()
PY
}

cleanup_backup_temp_files() {
  local path="$1"
  rm -f "$path" "$path-wal" "$path-shm"
}

if [[ "$is_memory_db" -eq 1 ]]; then
  echo "ℹ️ DATABASE_URL=:memory:，跳过数据库文件备份"
elif [[ "$DATABASE_URL" != sqlite* ]]; then
  echo "ℹ️ 当前 DATABASE_URL 不是 SQLite，跳过数据库文件备份"
elif [ -f "$DB_PATH" ]; then
  DB_BACKUP_PATH="$BACKUP_DIR/bot.db.$TS"
  DB_BACKUP_TMP="$BACKUP_DIR/.bot.db.$TS.tmp"
  cleanup_backup_temp_files "$DB_BACKUP_TMP"
  if sqlite_hot_backup "$DB_PATH" "$DB_BACKUP_TMP"; then
    if ! sqlite_validate_backup "$DB_BACKUP_TMP"; then
      cleanup_backup_temp_files "$DB_BACKUP_TMP"
      echo "❌ 数据库备份完整性校验失败" >&2
      exit 1
    fi
    mv "$DB_BACKUP_TMP" "$DB_BACKUP_PATH"
    cleanup_backup_temp_files "$DB_BACKUP_TMP"
    echo "✅ 已热备份数据库到 $DB_BACKUP_PATH"
  else
    cleanup_backup_temp_files "$DB_BACKUP_TMP"
    echo "❌ 数据库热备份失败，未生成备份文件" >&2
    exit 1
  fi
else
  echo "⚠️ 未找到数据库文件：$DB_PATH" >&2
fi

if [ -z "${ENCRYPTION_KEY:-}" ]; then
  KEY_PATH="${ENCRYPTION_KEY_FILE:-$DEFAULT_KEY_PATH}"
  if [ -f "$KEY_PATH" ]; then
    KEY_BACKUP_PATH="$BACKUP_DIR/encryption.key.$TS"
    KEY_BACKUP_TMP="$BACKUP_DIR/.encryption.key.$TS.tmp"
    cp "$KEY_PATH" "$KEY_BACKUP_TMP"
    chmod 600 "$KEY_BACKUP_TMP" || true
    mv "$KEY_BACKUP_TMP" "$KEY_BACKUP_PATH"
    echo "✅ 已备份密钥文件到 $KEY_BACKUP_PATH"
  else
    echo "⚠️ 未找到密钥文件：$KEY_PATH" >&2
  fi
else
  echo "ℹ️ 使用环境变量 ENCRYPTION_KEY，跳过密钥文件备份"
fi
