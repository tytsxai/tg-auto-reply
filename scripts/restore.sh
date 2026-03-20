#!/bin/bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ROOT_DIR/.env"
  set +a
fi

usage() {
  cat <<'USAGE'
用法：
  ./scripts/restore.sh <数据库备份文件> [密钥备份文件]

示例：
  ./scripts/restore.sh backups/bot.db.20260212-030000 backups/encryption.key.20260212-030000
USAGE
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  usage
  exit 2
fi

DB_BACKUP_SOURCE="$1"
KEY_BACKUP_SOURCE="${2:-}"

if [ ! -f "$DB_BACKUP_SOURCE" ]; then
  echo "❌ 数据库备份文件不存在：$DB_BACKUP_SOURCE" >&2
  exit 2
fi

DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./data/bot.db}"
DEFAULT_KEY_PATH="$ROOT_DIR/data/encryption.key"

resolve_sqlite_db_path() {
  local db_url="$1"
  local resolved=""

  if [[ "$db_url" != sqlite* ]]; then
    echo ""
    return 0
  fi

  resolved="$db_url"
  resolved="${resolved#sqlite+aiosqlite://}"
  resolved="${resolved#sqlite://}"

  if [[ "$resolved" == ":memory:" ]]; then
    echo ":memory:"
    return 0
  elif [[ "$resolved" == /./* ]]; then
    echo "$ROOT_DIR/${resolved#/./}"
    return 0
  elif [[ "$resolved" == //* ]]; then
    while [[ "$resolved" == /* ]]; do
      resolved="${resolved#/}"
    done
    echo "/$resolved"
    return 0
  elif [[ "$resolved" == /* ]]; then
    echo "$resolved"
    return 0
  fi

  echo "$ROOT_DIR/${resolved#./}"
}


resolve_lock_path() {
  local db_path="$1"
  local lock_path="${INSTANCE_LOCK_FILE:-}"
  if [ -n "$lock_path" ]; then
    echo "$lock_path"
    return 0
  fi
  if [ -n "$db_path" ] && [ "$db_path" != ":memory:" ]; then
    echo "$db_path.lock"
    return 0
  fi
  echo "$ROOT_DIR/data/bot.lock"
}

ensure_service_stopped() {
  local lock_path="$1"
  if [ -z "$lock_path" ]; then
    return 0
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "⚠️ 未找到 python3，无法检测实例锁，请确保服务已停止后再恢复" >&2
    return 0
  fi

  set +e
  python3 - "$lock_path" <<'PYLOCK'
import os
import sys

path = sys.argv[1]

try:
    import fcntl
except ImportError:
    raise SystemExit(0)

parent = os.path.dirname(path)
if parent:
    os.makedirs(parent, exist_ok=True)

with open(path, "a+") as fh:
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(2)
    except OSError:
        raise SystemExit(0)
PYLOCK
  rc=$?
  set -e

  if [ "$rc" -eq 0 ]; then
    return 0
  fi

  if [ "$rc" -eq 2 ]; then
    echo "❌ 检测到实例锁被占用：$lock_path" >&2
    echo "   请先停止正在运行的服务后再执行恢复。" >&2
    exit 1
  fi

  echo "⚠️ 无法可靠检测实例锁状态，请确认服务已停止：$lock_path" >&2
}

validate_sqlite_file() {
  local path="$1"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 未找到 python3，无法校验 SQLite 备份完整性" >&2
    return 1
  fi

  python3 - "$path" <<'PY'
import sqlite3
import sys

path = sys.argv[1]
conn = sqlite3.connect(path)
try:
    row = conn.execute("PRAGMA quick_check").fetchone()
    if not row or str(row[0]).lower() != "ok":
        raise SystemExit(1)
finally:
    conn.close()
PY
}

atomic_replace_file() {
  local source="$1"
  local target="$2"
  local ts="$3"

  local target_dir
  target_dir="$(dirname "$target")"
  mkdir -p "$target_dir"

  if [ -f "$target" ]; then
    local pre_restore_backup="$target.pre-restore.$ts"
    cp "$target" "$pre_restore_backup"
    chmod 600 "$pre_restore_backup" || true
    echo "ℹ️ 已生成恢复前快照：$pre_restore_backup"
  fi

  local tmp_target="$target.restore.$ts.tmp"
  cp "$source" "$tmp_target"
  chmod 600 "$tmp_target" || true
  mv "$tmp_target" "$target"
}

DB_PATH="$(resolve_sqlite_db_path "$DATABASE_URL")"
if [[ "$DATABASE_URL" != sqlite* ]]; then
  echo "❌ restore.sh 当前仅支持 SQLite。当前 DATABASE_URL=$DATABASE_URL" >&2
  exit 2
fi
if [[ "$DB_PATH" == ":memory:" ]]; then
  echo "❌ DATABASE_URL=:memory: 无法执行文件恢复" >&2
  exit 2
fi

LOCK_PATH="$(resolve_lock_path "$DB_PATH")"
ensure_service_stopped "$LOCK_PATH"

if ! validate_sqlite_file "$DB_BACKUP_SOURCE"; then
  echo "❌ 数据库备份完整性校验失败：$DB_BACKUP_SOURCE" >&2
  exit 1
fi

TS="$(date +%Y%m%d-%H%M%S)"
atomic_replace_file "$DB_BACKUP_SOURCE" "$DB_PATH" "$TS"

if ! validate_sqlite_file "$DB_PATH"; then
  echo "❌ 恢复后的数据库校验失败，目标文件：$DB_PATH" >&2
  exit 1
fi

echo "✅ 数据库已恢复：$DB_PATH"

if [ -n "$KEY_BACKUP_SOURCE" ]; then
  if [ ! -f "$KEY_BACKUP_SOURCE" ]; then
    echo "❌ 密钥备份文件不存在：$KEY_BACKUP_SOURCE" >&2
    exit 2
  fi

  KEY_PATH="${ENCRYPTION_KEY_FILE:-$DEFAULT_KEY_PATH}"
  atomic_replace_file "$KEY_BACKUP_SOURCE" "$KEY_PATH" "$TS"
  echo "✅ 密钥文件已恢复：$KEY_PATH"
elif [ -n "${ENCRYPTION_KEY:-}" ]; then
  echo "ℹ️ 当前使用 ENCRYPTION_KEY 环境变量，未恢复密钥文件"
else
  echo "ℹ️ 未提供密钥备份文件；如需恢复请追加第二个参数"
fi

echo "🎯 恢复完成。请在重启服务前确认应用进程已停止。"
echo "⚠️  建议在重启前执行数据库迁移以防 schema 版本不一致："
echo "    python scripts/migrate.py"
