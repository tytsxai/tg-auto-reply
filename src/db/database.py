"""数据库连接管理"""

import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from .models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")
DB_BUSY_TIMEOUT_MS = int(os.getenv("DB_BUSY_TIMEOUT_MS", "30000"))
DB_JOURNAL_MODE = os.getenv("DB_JOURNAL_MODE", "WAL")
DB_SYNCHRONOUS = os.getenv("DB_SYNCHRONOUS", "NORMAL")
SCHEMA_VERSION = 1

engine_kwargs = {"echo": False, "pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"timeout": max(DB_BUSY_TIMEOUT_MS, 1000) / 1000}

engine = create_async_engine(DATABASE_URL, **engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA journal_mode={DB_JOURNAL_MODE}")
            cursor.execute(f"PRAGMA synchronous={DB_SYNCHRONOUS}")
            cursor.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


async def init_db():
    """初始化数据库"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_schema_version(conn)


async def _get_schema_version(conn) -> int:
    if DATABASE_URL.startswith("sqlite"):
        result = await conn.exec_driver_sql("PRAGMA user_version")
        version = result.scalar()
        return int(version or 0)
    await conn.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, "
        "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    result = await conn.exec_driver_sql("SELECT MAX(version) FROM schema_migrations")
    version = result.scalar()
    return int(version or 0)


async def _set_schema_version(conn, version: int) -> None:
    if DATABASE_URL.startswith("sqlite"):
        await conn.exec_driver_sql(f"PRAGMA user_version = {version}")
        return
    await conn.exec_driver_sql(
        "INSERT INTO schema_migrations (version) VALUES (:version)", {"version": version}
    )


async def ensure_schema_version(conn) -> int:
    """确保数据库 schema 版本一致。"""
    current = await _get_schema_version(conn)
    if current == 0:
        await _set_schema_version(conn, SCHEMA_VERSION)
        return SCHEMA_VERSION
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本({current})高于当前程序版本({SCHEMA_VERSION})，请升级程序。"
        )
    if current < SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本({current})过旧，请先运行 scripts/migrate.py 升级。"
        )
    return current


async def get_schema_version() -> int:
    """读取数据库 schema 版本。"""
    async with engine.begin() as conn:
        return await _get_schema_version(conn)


async def verify_schema_version() -> None:
    """验证数据库 schema 版本，异常时抛出错误。"""
    async with engine.begin() as conn:
        current = await _get_schema_version(conn)
    if current == 0:
        raise RuntimeError("数据库 schema 版本未初始化，请运行 scripts/migrate.py")
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本({current})高于当前程序版本({SCHEMA_VERSION})，请升级程序。"
        )
    if current < SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本({current})过旧，请先运行 scripts/migrate.py 升级。"
        )


async def migrate_db(target_version: int | None = None) -> int:
    """执行数据库迁移（当前仅支持版本标记初始化）。"""
    target = target_version or SCHEMA_VERSION
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        current = await _get_schema_version(conn)
        if current > target:
            raise RuntimeError(
                f"数据库版本({current})高于目标版本({target})，禁止降级。"
            )
        if current == target:
            return current
        if current == 0:
            await _set_schema_version(conn, target)
            return target
        raise RuntimeError(
            f"数据库版本({current})过旧，需补充迁移脚本后再升级到 {target}。"
        )


async def get_session() -> AsyncSession:
    """获取数据库会话"""
    async with async_session() as session:
        yield session
