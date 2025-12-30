from __future__ import annotations

import importlib

import pytest_asyncio
from cryptography.fernet import Fernet


@pytest_asyncio.fixture()
async def db_env(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUTO_REPLY_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("MAX_CONCURRENT_REPLIES", "1")
    monkeypatch.setenv("MAX_PENDING_REPLY_TASKS", "10")

    import src.db.database as database

    database = importlib.reload(database)
    import src.db as db_pkg

    db_pkg = importlib.reload(db_pkg)
    import src.bot.handlers as handlers

    handlers = importlib.reload(handlers)

    await database.init_db()

    yield {"database": database, "db": db_pkg, "handlers": handlers}

    await database.engine.dispose()
