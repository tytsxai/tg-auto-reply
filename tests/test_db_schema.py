from __future__ import annotations

import importlib

import pytest


@pytest.mark.asyncio
async def test_schema_version_initialized(db_env):
    database = db_env["database"]
    version = await database.get_schema_version()
    assert version == database.SCHEMA_VERSION


@pytest.mark.asyncio
async def test_migrate_db_sets_version(tmp_path, monkeypatch):
    db_path = tmp_path / "migrate.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import src.db.database as database

    database = importlib.reload(database)

    version = await database.migrate_db()
    assert version == database.SCHEMA_VERSION
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_verify_schema_version_requires_init(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import src.db.database as database

    database = importlib.reload(database)

    with pytest.raises(RuntimeError):
        await database.verify_schema_version()

    await database.engine.dispose()
