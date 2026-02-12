from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _env_with_path(base: dict[str, str], root: Path) -> dict[str, str]:
    env = dict(base)
    env["PATH"] = f"{root}:{env.get('PATH', '')}"
    return env


def _test_python(root: Path) -> str:
    venv_python = root / ".venv" / "bin" / "python"
    if venv_python.exists():
        probe = subprocess.run(
            [str(venv_python), "-c", "import greenlet"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return str(venv_python)
    return sys.executable


def test_backup_script_loads_dotenv_and_custom_sqlite_path(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "backup.sh"
    backup_dir = tmp_path / "backups"

    custom_db = tmp_path / "custom" / "runtime.db"
    custom_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(custom_db)
    try:
        conn.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO demo(v) VALUES ('db-content')")
        conn.commit()
    finally:
        conn.close()

    default_db_dir = root / "data"
    default_db_dir.mkdir(parents=True, exist_ok=True)
    default_db_path = default_db_dir / "bot.db"
    had_default_db = default_db_path.exists()
    default_db_content = default_db_path.read_bytes() if had_default_db else None

    env_path = root / ".env"
    had_env = env_path.exists()
    old_env = env_path.read_text(encoding="utf-8") if had_env else None

    try:
        default_db_path.write_text("default-db", encoding="utf-8")
        env_path.write_text(
            f"DATABASE_URL=sqlite+aiosqlite:///{custom_db}\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            ["bash", str(script), str(backup_dir)],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        assert completed.returncode == 0

        backups = sorted(backup_dir.glob("bot.db.*"))
        assert backups
        latest = backups[-1]
        assert latest.stat().st_mode & 0o077 == 0
        conn = sqlite3.connect(latest)
        try:
            # 文件必须是可读取的有效 sqlite 备份，且包含预期数据
            value = conn.execute("SELECT v FROM demo LIMIT 1").fetchone()[0]
        finally:
            conn.close()
        assert value == "db-content"
    finally:
        if had_env and old_env is not None:
            env_path.write_text(old_env, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()

        if had_default_db and default_db_content is not None:
            default_db_path.write_bytes(default_db_content)
        elif default_db_path.exists():
            default_db_path.unlink()


def test_backup_script_fails_when_hot_backup_fails(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "backup.sh"

    custom_db = tmp_path / "missing" / "runtime.db"
    backup_dir = tmp_path / "backups"

    env_path = root / ".env"
    had_env = env_path.exists()
    old_env = env_path.read_text(encoding="utf-8") if had_env else None

    try:
        env_path.write_text(
            f"DATABASE_URL=sqlite+aiosqlite:///{custom_db}\n",
            encoding="utf-8",
        )
        # 要求数据库路径存在且不可读，确保热备份失败。
        custom_db.parent.mkdir(parents=True, exist_ok=True)
        custom_db.write_text("", encoding="utf-8")
        custom_db.chmod(0o000)
        completed = subprocess.run(
            ["bash", str(script), str(tmp_path / "backups")],
            cwd=root,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        assert completed.returncode != 0
        assert "备份" in completed.stderr
        assert not list(backup_dir.glob("bot.db.*"))
        assert not list(backup_dir.glob(".bot.db.*"))
    finally:
        if custom_db.exists():
            custom_db.chmod(0o600)
        if had_env and old_env is not None:
            env_path.write_text(old_env, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()


def test_backup_script_uses_python_fallback_without_sqlite3(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "backup.sh"
    backup_dir = tmp_path / "backups"

    db_path = tmp_path / "fallback" / "runtime.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO demo(v) VALUES ('ok')")
        conn.commit()
    finally:
        conn.close()

    env_path = root / ".env"
    had_env = env_path.exists()
    old_env = env_path.read_text(encoding="utf-8") if had_env else None

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        f"#!/bin/bash\nexec '{sys.executable}' \"$@\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    fake_sqlite = fake_bin / "sqlite3"
    fake_sqlite.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    fake_sqlite.chmod(0o755)

    try:
        env_path.write_text(
            f"DATABASE_URL=sqlite+aiosqlite:///{db_path}\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

        completed = subprocess.run(
            ["/bin/bash", str(script), str(backup_dir)],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        assert completed.returncode == 0
        assert "Python 回退" in completed.stderr
        backups = sorted(backup_dir.glob("bot.db.*"))
        assert backups
        assert not list(backup_dir.glob(".bot.db.*"))

        backup_conn = sqlite3.connect(backups[-1])
        try:
            value = backup_conn.execute("SELECT v FROM demo LIMIT 1").fetchone()[0]
        finally:
            backup_conn.close()
        assert value == "ok"
    finally:
        if had_env and old_env is not None:
            env_path.write_text(old_env, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()


def test_check_alerts_script_reads_log_file_from_dotenv(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "check_alerts.sh"
    log_file = tmp_path / "runtime.log"
    log_file.write_text("all good\n", encoding="utf-8")

    env_path = root / ".env"
    had_env = env_path.exists()
    old_env = env_path.read_text(encoding="utf-8") if had_env else None

    try:
        env_path.write_text(
            f"LOG_FILE={log_file}\nALERT_KEYWORDS=critical-error\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            ["bash", str(script)],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        assert completed.returncode == 0
        assert "告警检查通过" in completed.stdout
    finally:
        if had_env and old_env is not None:
            env_path.write_text(old_env, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()


def test_install_cron_script_uses_dotenv_default_retention(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "install_cron.sh"
    fake_crontab = tmp_path / "crontab"
    fake_crontab.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "if [ \"${1:-}\" = \"-l\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "cat > /dev/null\n",
        encoding="utf-8",
    )
    fake_crontab.chmod(0o755)

    env_path = root / ".env"
    had_env = env_path.exists()
    old_env = env_path.read_text(encoding="utf-8") if had_env else None

    try:
        env_path.write_text("LOG_RETENTION_DAYS=66\n", encoding="utf-8")

        completed = subprocess.run(
            ["bash", str(script)],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=_env_with_path(os.environ.copy(), tmp_path),
        )
        assert completed.returncode == 0
        assert "cleanup_logs.py 66" in completed.stdout
    finally:
        if had_env and old_env is not None:
            env_path.write_text(old_env, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()


def test_migrate_script_loads_dotenv_before_import(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "migrate.py"
    db_path = tmp_path / "migration-script.db"

    env_path = root / ".env"
    had_env = env_path.exists()
    old_env = env_path.read_text(encoding="utf-8") if had_env else None

    try:
        env_path.write_text(
            f"DATABASE_URL=sqlite+aiosqlite:///{db_path}\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [_test_python(root), str(script)],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        assert completed.returncode == 0
        assert db_path.exists()
        assert "数据库迁移完成" in completed.stdout
    finally:
        if had_env and old_env is not None:
            env_path.write_text(old_env, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()


def test_restore_script_restores_db_and_key(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "restore.sh"

    backup_db = tmp_path / "backup.db"
    conn = sqlite3.connect(backup_db)
    try:
        conn.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO demo(v) VALUES ('from-backup')")
        conn.commit()
    finally:
        conn.close()

    live_db = tmp_path / "runtime" / "bot.db"
    live_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(live_db)
    try:
        conn.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO demo(v) VALUES ('from-live')")
        conn.commit()
    finally:
        conn.close()

    backup_key = tmp_path / "backup.key"
    backup_key.write_text("new-key", encoding="utf-8")
    live_key = tmp_path / "runtime" / "encryption.key"
    live_key.write_text("old-key", encoding="utf-8")

    env_path = root / ".env"
    had_env = env_path.exists()
    old_env = env_path.read_text(encoding="utf-8") if had_env else None

    try:
        env_path.write_text(
            "\n".join(
                [
                    f"DATABASE_URL=sqlite+aiosqlite:///{live_db}",
                    f"ENCRYPTION_KEY_FILE={live_key}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            ["bash", str(script), str(backup_db), str(backup_key)],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        assert completed.returncode == 0

        conn = sqlite3.connect(live_db)
        try:
            value = conn.execute("SELECT v FROM demo LIMIT 1").fetchone()[0]
        finally:
            conn.close()
        assert value == "from-backup"
        assert live_key.read_text(encoding="utf-8") == "new-key"

        db_pre_restore = list(live_db.parent.glob("bot.db.pre-restore.*"))
        key_pre_restore = list(live_key.parent.glob("encryption.key.pre-restore.*"))
        assert db_pre_restore
        assert key_pre_restore
    finally:
        if had_env and old_env is not None:
            env_path.write_text(old_env, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()


def test_restore_script_rejects_invalid_backup(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "restore.sh"

    invalid_backup = tmp_path / "invalid.db"
    invalid_backup.write_text("not-a-sqlite-file", encoding="utf-8")
    live_db = tmp_path / "runtime" / "bot.db"

    env_path = root / ".env"
    had_env = env_path.exists()
    old_env = env_path.read_text(encoding="utf-8") if had_env else None

    try:
        env_path.write_text(
            f"DATABASE_URL=sqlite+aiosqlite:///{live_db}\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            ["bash", str(script), str(invalid_backup)],
            cwd=root,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )

        assert completed.returncode != 0
        assert "校验失败" in completed.stderr
    finally:
        if had_env and old_env is not None:
            env_path.write_text(old_env, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()
