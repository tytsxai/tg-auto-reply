from __future__ import annotations

import os
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
        return str(venv_python)
    return sys.executable


def test_backup_script_loads_dotenv_and_custom_sqlite_path(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "backup.sh"
    backup_dir = tmp_path / "backups"

    custom_db = tmp_path / "custom" / "runtime.db"
    custom_db.parent.mkdir(parents=True, exist_ok=True)
    custom_db.write_text("db-content", encoding="utf-8")

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
        assert latest.read_text(encoding="utf-8") == "db-content"
    finally:
        if had_env and old_env is not None:
            env_path.write_text(old_env, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()

        if had_default_db and default_db_content is not None:
            default_db_path.write_bytes(default_db_content)
        elif default_db_path.exists():
            default_db_path.unlink()


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
