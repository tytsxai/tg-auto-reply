from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError


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


@pytest.mark.asyncio
async def test_migrate_db_dedupes_contact_lists(tmp_path, monkeypatch):
    db_path = tmp_path / "dedupe.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import src.db.database as database

    database = importlib.reload(database)

    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA user_version = 2")
        await conn.exec_driver_sql(
            "DROP INDEX IF EXISTS idx_contact_lists_user_type_contact"
        )
        await conn.exec_driver_sql(
            "INSERT INTO users (id, telegram_id, is_active, created_at, updated_at) "
            "VALUES (:id, :telegram_id, :is_active, :created_at, :updated_at)",
            {
                "id": 1,
                "telegram_id": 9001,
                "is_active": 0,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )
        payload = {
            "user_id": 1,
            "list_type": "whitelist",
            "contact_id": 111,
            "contact_name": "A",
            "created_at": datetime.now(timezone.utc),
        }
        await conn.exec_driver_sql(
            "INSERT INTO contact_lists "
            "(user_id, list_type, contact_id, contact_name, created_at) "
            "VALUES (:user_id, :list_type, :contact_id, :contact_name, :created_at)",
            payload,
        )
        await conn.exec_driver_sql(
            "INSERT INTO contact_lists "
            "(user_id, list_type, contact_id, contact_name, created_at) "
            "VALUES (:user_id, :list_type, :contact_id, :contact_name, :created_at)",
            payload,
        )

    version = await database.migrate_db(target_version=3)
    assert version == 3

    async with database.engine.begin() as conn:
        result = await conn.exec_driver_sql(
            "SELECT COUNT(*) FROM contact_lists WHERE user_id=1 AND list_type='whitelist' "
            "AND contact_id=111"
        )
        assert result.scalar_one() == 1

        with pytest.raises(IntegrityError):
            await conn.exec_driver_sql(
                "INSERT INTO contact_lists "
                "(user_id, list_type, contact_id, contact_name, created_at) "
                "VALUES (:user_id, :list_type, :contact_id, :contact_name, :created_at)",
                payload,
            )

    await database.engine.dispose()
