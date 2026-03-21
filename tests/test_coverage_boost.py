"""补充测试：覆盖 db/database.py、client/manager.py、ai/chat.py、monitoring/health.py 低覆盖路径。"""
from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

UTC = timezone.utc


# ---------------------------------------------------------------------------
# src/db/database.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_db_v1_to_v2(tmp_path, monkeypatch):
    """schema v1 -> v2 升级路径（只升到 target=2）。"""
    db_path = tmp_path / "v1to2.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import src.db.database as database
    database = importlib.reload(database)

    # 先建表，再手动设版本为 1
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA user_version = 1")

    version = await database.migrate_db(target_version=2)
    assert version == 2
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_migrate_db_v1_to_v3(tmp_path, monkeypatch):
    """schema v1 -> v3 完整升级路径。"""
    db_path = tmp_path / "v1to3.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import src.db.database as database
    database = importlib.reload(database)

    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA user_version = 1")

    version = await database.migrate_db(target_version=3)
    assert version == 3
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_migrate_db_downgrade_raises(tmp_path, monkeypatch):
    """当前版本高于目标版本时应抛出 RuntimeError（禁止降级）。"""
    db_path = tmp_path / "downgrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import src.db.database as database
    database = importlib.reload(database)

    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA user_version = 3")

    with pytest.raises(RuntimeError, match="禁止降级"):
        await database.migrate_db(target_version=2)
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_migrate_db_too_old_raises(tmp_path, monkeypatch):
    """版本 4 -> target 5 时没有对应迁移脚本，应抛出 RuntimeError。"""
    db_path = tmp_path / "tooold.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import src.db.database as database
    database = importlib.reload(database)

    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        # 伪造版本 4，target 默认为 SCHEMA_VERSION(3)，触发 downgrade 路径
        await conn.exec_driver_sql("PRAGMA user_version = 4")

    with pytest.raises(RuntimeError):
        await database.migrate_db()
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_migrate_db_already_at_target(tmp_path, monkeypatch):
    """当前版本已等于目标版本，直接返回。"""
    db_path = tmp_path / "sameversion.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import src.db.database as database
    database = importlib.reload(database)

    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA user_version = 3")

    version = await database.migrate_db(target_version=3)
    assert version == 3
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_verify_schema_version_ok(tmp_path, monkeypatch):
    """verify_schema_version 在版本匹配时正常返回。"""
    db_path = tmp_path / "verifyok.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import src.db.database as database
    database = importlib.reload(database)

    await database.migrate_db()
    # 不应抛出
    await database.verify_schema_version()
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_ensure_greenlet_raises_when_missing(monkeypatch):
    """greenlet 缺失时 _ensure_greenlet_available 应抛出 RuntimeError。"""
    import src.db.database as database

    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "greenlet":
            raise ModuleNotFoundError("No module named 'greenlet'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with pytest.raises(RuntimeError, match="greenlet"):
            database._ensure_greenlet_available()


# ---------------------------------------------------------------------------
# src/ai/chat.py — 熔断器恢复路径
# ---------------------------------------------------------------------------


def test_circuit_breaker_auto_recover():
    """_is_circuit_open: open_until 已过期时应自动关闭并返回 False。"""
    from src.ai import chat

    # 设置一个过去的 open_until
    chat._circuit_open_until = datetime.now(UTC) - timedelta(seconds=1)
    result = chat._is_circuit_open()
    assert result is False
    assert chat._circuit_open_until is None


def test_circuit_breaker_opens_at_threshold():
    """连续失败达到阈值时熔断器应打开。"""
    from src.ai import chat

    chat._circuit_failure_count = 0
    chat._circuit_open_until = None
    threshold = chat._CIRCUIT_FAILURE_THRESHOLD

    for _ in range(threshold):
        chat._record_failure()

    assert chat._circuit_open_until is not None
    assert chat._circuit_open_until > datetime.now(UTC)
    # 失败计数在打开后重置
    assert chat._circuit_failure_count == 0
    # 清理
    chat._circuit_open_until = None


@pytest.mark.asyncio
async def test_generate_reply_strips_think_tags(monkeypatch):
    """AI 回复中的 <think>...</think> 标签应被过滤。"""
    import types
    from src.ai import chat

    chat._circuit_open_until = None
    chat._circuit_failure_count = 0

    class StubResponse:
        choices = [types.SimpleNamespace(
            message=types.SimpleNamespace(content="<think>内部推理</think>实际回复")
        )]

    class StubCompletions:
        async def create(self, **kwargs):
            return StubResponse()

    class StubClient:
        chat = types.SimpleNamespace(completions=StubCompletions())

    monkeypatch.setattr(chat, "get_client", lambda: StubClient())
    reply = await chat.generate_reply(
        message="test", sender_name="X", context=[], system_prompt=None
    )
    assert "think" not in reply
    assert reply == "实际回复"


@pytest.mark.asyncio
async def test_generate_reply_empty_after_strip_uses_fallback(monkeypatch):
    """过滤 think 标签后内容为空时应返回兜底文案。"""
    import types
    from src.ai import chat

    chat._circuit_open_until = None
    chat._circuit_failure_count = 0

    class StubResponse:
        choices = [types.SimpleNamespace(
            message=types.SimpleNamespace(content="<think>全是推理</think>")
        )]

    class StubCompletions:
        async def create(self, **kwargs):
            return StubResponse()

    class StubClient:
        chat = types.SimpleNamespace(completions=StubCompletions())

    monkeypatch.setattr(chat, "get_client", lambda: StubClient())
    reply = await chat.generate_reply(
        message="test", sender_name="X", context=[], system_prompt=None
    )
    assert reply  # 不应为空


# ---------------------------------------------------------------------------
# src/client/manager.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_task_done_logs_exception():
    """任务以异常退出时 _handle_task_done 应记录日志并调用 _schedule_mark_inactive。"""
    from src.client.manager import ClientManager

    manager = ClientManager()
    scheduled = []
    manager._schedule_mark_inactive = lambda uid: scheduled.append(uid)  # type: ignore

    async def boom():
        raise RuntimeError("task boom")

    task = asyncio.create_task(boom())
    manager._tasks[99] = task
    await asyncio.sleep(0.05)  # 让任务完成

    manager._handle_task_done(99, task)
    assert 99 in scheduled


@pytest.mark.asyncio
async def test_handle_task_done_cancelled_cleans_task_dict():
    """任务被取消时 _handle_task_done 应从 _tasks 中移除该任务。"""
    from src.client.manager import ClientManager

    manager = ClientManager()
    # 取消时 finally 仍会调用 _schedule_mark_inactive，用空实现避免副作用
    manager._schedule_mark_inactive = lambda uid: None  # type: ignore

    async def forever():
        await asyncio.sleep(100)

    task = asyncio.create_task(forever())
    manager._tasks[88] = task
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    manager._handle_task_done(88, task)
    # 任务应已从 _tasks 中移除
    assert 88 not in manager._tasks


@pytest.mark.asyncio
async def test_schedule_mark_inactive_no_loop():
    """在没有运行事件循环时 _schedule_mark_inactive 应静默返回。"""
    from src.client.manager import ClientManager
    import threading

    manager = ClientManager()
    errors = []

    def run_in_thread():
        try:
            # 线程中无 running loop
            manager._schedule_mark_inactive(42)
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=run_in_thread)
    t.start()
    t.join()
    assert errors == []


@pytest.mark.asyncio
async def test_mark_user_inactive_sets_flag(db_env):
    """_mark_user_inactive 应将 user.is_active 设为 False。"""
    import importlib
    from sqlalchemy import select

    db = db_env["db"]
    database = db_env["database"]

    async with db.async_session() as session:
        user = db.User(telegram_id=5001, is_active=True)
        session.add(user)
        await session.commit()
        uid = user.id

    # 直接 reload manager 确保使用测试 DB
    import src.client.manager as mgr_mod
    mgr_mod = importlib.reload(mgr_mod)
    manager = mgr_mod.ClientManager()

    await manager._mark_user_inactive(5001)

    async with db.async_session() as session:
        result = await session.execute(
            select(db.User).where(db.User.telegram_id == 5001)
        )
        user = result.scalar_one()
        assert user.is_active is False


# ---------------------------------------------------------------------------
# src/monitoring/health.py — _db_check 异常路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_check_returns_false_on_exception(monkeypatch):
    """数据库异常时 _db_check 应返回 (False, None)。"""
    import importlib
    import src.monitoring.health as health
    health = importlib.reload(health)

    server = health.HealthServer("127.0.0.1", 8080)

    async def bad_session(*args, **kwargs):
        raise RuntimeError("db down")

    # patch async_session 上下文管理器
    class FakeCM:
        async def __aenter__(self):
            raise RuntimeError("db down")
        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(health, "async_session", lambda: FakeCM())

    ok, version = await server._db_check()
    assert ok is False
    assert version is None


@pytest.mark.asyncio
async def test_ready_returns_503_when_db_fails(monkeypatch):
    """DB 检查失败时 /readyz 应返回 503。"""
    import importlib
    import src.monitoring.health as health
    health = importlib.reload(health)

    server = health.HealthServer("127.0.0.1", 8080)

    async def fake_db_check():
        return False, None

    monkeypatch.setattr(server, "_db_check", fake_db_check)
    monkeypatch.setenv("ENABLE_ASYNC_LOGGING", "0")

    resp = await server._handle_ready(make_mocked_request("GET", "/readyz"))
    assert resp.status == 503
