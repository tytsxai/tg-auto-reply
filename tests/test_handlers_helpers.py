from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import DummyMessage, DummyUpdate
from sqlalchemy import select
import asyncio

UTC = timezone.utc


@pytest.mark.asyncio
async def test_build_context_and_should_reply(db_env):
    handlers = db_env["handlers"]
    db = db_env["db"]

    async with db.async_session() as session:
        user = db.User(telegram_id=1, is_active=True)
        session.add(user)
        await session.flush()

        settings = db.UserSettings(user_id=user.id, ai_enabled=True, auto_reply_groups=True)
        session.add(settings)

        now = datetime.now(UTC)
        session.add_all(
            [
                db.MessageLog(
                    user_id=user.id,
                    chat_id=123,
                    original_message="hi",
                    ai_reply="hello",
                    status="sent",
                    created_at=now - timedelta(seconds=2),
                ),
                db.MessageLog(
                    user_id=user.id,
                    chat_id=123,
                    original_message="how are you",
                    ai_reply="fine",
                    status="sent",
                    created_at=now - timedelta(seconds=1),
                ),
            ]
        )
        await session.commit()

        context = await handlers._build_context(session, user.id, 123)
        assert context[0]["content"] == "hi"
        assert context[-1]["content"] == "fine"

        class DummyEvent:
            is_group = False
            is_channel = False
            is_private = True
            sender_id = 999
            chat_id = 999

    should = await handlers._should_reply(session, user.id, settings, DummyEvent())
    assert should is True

    settings.blacklist_enabled = True
    session.add(db.ContactList(user_id=user.id, list_type="blacklist", contact_id=999))
    await session.commit()
    should = await handlers._should_reply(session, user.id, settings, DummyEvent())
    assert should is False

    class GroupEvent:
        is_group = True
        is_channel = False
        is_private = False
        sender_id = 999
        chat_id = 999

    settings.blacklist_enabled = False
    settings.auto_reply_groups = False
    await session.commit()
    should = await handlers._should_reply(session, user.id, settings, GroupEvent())
    assert should is False

    settings.auto_reply_groups = True
    settings.whitelist_only = True
    session.add(db.ContactList(user_id=user.id, list_type="whitelist", contact_id=999))
    await session.commit()
    should = await handlers._should_reply(session, user.id, settings, GroupEvent())
    assert should is True


def test_format_helpers(db_env):
    handlers = db_env["handlers"]

    assert handlers._normalize_bool_arg("on") is True
    assert handlers._normalize_bool_arg("OFF") is False
    assert handlers._normalize_bool_arg("maybe") is None

    class DummySender:
        first_name = "A"
        last_name = "B"
        username = "ab"

    assert handlers._format_sender_name(DummySender()) == "A B"
    assert handlers._format_contact_name("", 123) == "ID:123"


@pytest.mark.asyncio
async def test_delete_sensitive_message_and_clear_context(db_env):
    handlers = db_env["handlers"]

    class DummyMessage:
        def __init__(self) -> None:
            self.deleted = False

        async def delete(self) -> None:
            self.deleted = True

    update = DummyUpdate(message=DummyMessage())
    await handlers._delete_sensitive_message(update)
    assert update.message.deleted is True

    context = type("Ctx", (), {"user_data": {"api_id": 1, "client": object()}})()
    handlers._clear_login_context(context)
    assert context.user_data == {}


@pytest.mark.asyncio
async def test_resolve_contact_target_with_numeric(db_env):
    handlers = db_env["handlers"]
    update = DummyUpdate(message=DummyMessage("test"))

    contact_id, contact_name, error = await handlers._resolve_contact_target(
        update, "123456", telegram_id=1
    )

    assert error is None
    assert contact_id == 123456
    assert contact_name is None


@pytest.mark.asyncio
async def test_resolve_contact_target_missing_and_username(db_env, monkeypatch):
    handlers = db_env["handlers"]
    update = DummyUpdate(message=DummyMessage("test"))

    contact_id, contact_name, error = await handlers._resolve_contact_target(
        update, None, telegram_id=1
    )
    assert contact_id is None
    assert contact_name is None
    assert "请提供目标" in error

    monkeypatch.setattr(handlers.client_manager, "get_client", lambda _user_id: None)
    contact_id, contact_name, error = await handlers._resolve_contact_target(
        update, "@someone", telegram_id=1
    )
    assert contact_id is None
    assert contact_name is None
    assert "未找到可用" in error


@pytest.mark.asyncio
async def test_resolve_contact_target_username_success(db_env, monkeypatch):
    handlers = db_env["handlers"]

    class StubClient:
        def __init__(self):
            self.stopped = False

        async def connect(self):
            return True

        async def stop(self):
            self.stopped = True

        @property
        def client(self):
            return self

        async def get_entity(self, _raw):
            return type("Entity", (), {"id": 4242, "first_name": "Test"})()

    stub_client = StubClient()
    monkeypatch.setattr(handlers.client_manager, "get_client", lambda _user_id: stub_client)
    monkeypatch.setattr(handlers.client_manager, "is_running", lambda _user_id: False)
    monkeypatch.setattr(handlers, "get_display_name", lambda _entity: "Test")

    update = DummyUpdate(message=DummyMessage("test"))
    contact_id, contact_name, error = await handlers._resolve_contact_target(
        update, "@user", telegram_id=1
    )

    assert error is None
    assert contact_id == 4242
    assert contact_name == "Test"


@pytest.mark.asyncio
async def test_resolve_contact_target_username_failures(db_env, monkeypatch):
    handlers = db_env["handlers"]

    class StubClient:
        async def stop(self):
            return None

        async def connect(self):
            return False

    monkeypatch.setattr(handlers.client_manager, "get_client", lambda _user_id: StubClient())

    update = DummyUpdate(message=DummyMessage("test"))
    contact_id, contact_name, error = await handlers._resolve_contact_target(
        update, "@user", telegram_id=1
    )
    assert contact_id is None
    assert contact_name is None
    assert "登录已失效" in error

    class StubClient2:
        def __init__(self):
            self.stopped = False

        async def connect(self):
            return True

        async def stop(self):
            self.stopped = True

        @property
        def client(self):
            return self

        async def get_entity(self, _raw):
            raise RuntimeError("boom")

    stub_client = StubClient2()
    monkeypatch.setattr(handlers.client_manager, "get_client", lambda _user_id: stub_client)
    monkeypatch.setattr(handlers.client_manager, "is_running", lambda _user_id: False)

    contact_id, contact_name, error = await handlers._resolve_contact_target(
        update, "@user", telegram_id=1
    )
    assert contact_id is None
    assert contact_name is None
    assert "无法解析" in error


@pytest.mark.asyncio
async def test_pending_task_counters(db_env):
    handlers = db_env["handlers"]
    handlers._pending_reply_tasks = 0

    reserved, reason = await handlers._reserve_reply_task(1)
    assert reserved is True
    assert reason is None
    assert handlers.get_pending_reply_tasks() == 1

    await handlers._release_reply_task(1)
    assert handlers.get_pending_reply_tasks() == 0


@pytest.mark.asyncio
async def test_per_user_pending_limit(db_env, monkeypatch):
    handlers = db_env["handlers"]
    handlers._pending_reply_tasks = 0
    handlers._pending_reply_tasks_by_user.clear()

    monkeypatch.setattr(handlers, "MAX_PENDING_REPLY_TASKS", 10)
    monkeypatch.setattr(handlers, "MAX_PENDING_REPLY_TASKS_PER_USER", 1)

    reserved, reason = await handlers._reserve_reply_task(1)
    assert reserved is True
    assert reason is None

    reserved, reason = await handlers._reserve_reply_task(1)
    assert reserved is False
    assert reason == "per_user"

    reserved, reason = await handlers._reserve_reply_task(2)
    assert reserved is True
    assert reason is None

    await handlers._release_reply_task(1)
    await handlers._release_reply_task(2)
    handlers._pending_reply_tasks = 0


@pytest.mark.asyncio
async def test_wait_for_reply_tasks_timeout(db_env):
    handlers = db_env["handlers"]

    async def slow():
        await asyncio.sleep(1)

    task = asyncio.create_task(slow())
    handlers._active_reply_tasks.add(task)
    task.add_done_callback(lambda t: handlers._active_reply_tasks.discard(t))

    await handlers.wait_for_reply_tasks(timeout=0.01)
    await asyncio.sleep(0)
    assert task.done()


@pytest.mark.asyncio
async def test_cancel_reply_tasks_for_user(db_env):
    handlers = db_env["handlers"]

    async def slow():
        await asyncio.sleep(5)

    task1 = asyncio.create_task(slow())
    task2 = asyncio.create_task(slow())

    handlers._track_reply_task(42, task1)
    handlers._track_reply_task(42, task2)

    cancelled = await handlers._cancel_reply_tasks_for_user(42)
    await asyncio.sleep(0)

    assert cancelled == 2
    assert task1.cancelled()
    assert task2.cancelled()
    assert 42 not in handlers._user_reply_tasks
    assert task1 not in handlers._active_reply_tasks
    assert task2 not in handlers._active_reply_tasks


@pytest.mark.asyncio
async def test_async_log_worker_writes(db_env):
    handlers = db_env["handlers"]
    db = db_env["db"]

    async with db.async_session() as session:
        user = db.User(telegram_id=1000, is_active=True)
        session.add(user)
        await session.commit()
        user_id = user.id

    await handlers.stop_log_worker()
    await handlers.start_log_worker()
    try:
        await handlers._log_message(
            user_id=user_id,
            chat_id=123,
            chat_title="chat",
            sender_name="tester",
            original_message="hello",
            ai_reply="hi",
            status="sent",
        )
        await handlers.flush_log_queue(timeout=2.0)
    finally:
        await handlers.stop_log_worker()

    async with db.async_session() as session:
        result = await session.execute(
            select(db.MessageLog).where(db.MessageLog.user_id == user_id)
        )
        log = result.scalar_one()
        assert log.status == "sent"


@pytest.mark.asyncio
async def test_in_memory_cooldown_helpers(db_env, monkeypatch):
    handlers = db_env["handlers"]
    user_id = 1001
    chat_id = 2002

    monkeypatch.setattr(handlers, "AUTO_REPLY_COOLDOWN_SECONDS", 2)
    monkeypatch.setattr(handlers, "INFLIGHT_COOLDOWN_TTL_SECONDS", 10)
    await handlers._clear_user_cooldown_state(user_id)

    allowed = await handlers._check_schedule_cooldown(user_id, chat_id)
    assert allowed is True

    allowed = await handlers._check_schedule_cooldown(user_id, chat_id)
    assert allowed is False

    await handlers._mark_sent_now(user_id, chat_id)
    hit = await handlers._send_cooldown_hit(user_id, chat_id)
    assert hit is True

    await handlers._clear_user_cooldown_state(user_id)


@pytest.mark.asyncio
async def test_recent_context_cache_prunes(db_env, monkeypatch):
    handlers = db_env["handlers"]
    user_id = 1002

    await handlers._clear_user_context(user_id)
    handlers._recent_context.clear()
    monkeypatch.setattr(handlers, "_CONTEXT_CACHE_MAX_CHATS", 1)

    await handlers._update_recent_context(user_id, 1, "hi", "ok")
    await handlers._update_recent_context(user_id, 2, "hello", "ok2")

    first = await handlers._get_recent_context(user_id, 1, None)
    second = await handlers._get_recent_context(user_id, 2, None)

    assert first == []
    assert second

    await handlers._clear_user_context(user_id)


@pytest.mark.asyncio
async def test_chat_queue_serializes(db_env):
    handlers = db_env["handlers"]
    user_id = 1003
    chat_id = 3003

    await handlers._clear_user_chat_queue(user_id)

    first_guard = await handlers._enter_chat_queue(user_id, chat_id)
    entered = asyncio.Event()

    async def second_entry():
        guard = await handlers._enter_chat_queue(user_id, chat_id)
        entered.set()
        await handlers._exit_chat_queue(user_id, chat_id, guard)

    task = asyncio.create_task(second_entry())
    await asyncio.sleep(0)
    assert not entered.is_set()

    await handlers._exit_chat_queue(user_id, chat_id, first_guard)
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    await task

    await handlers._clear_user_chat_queue(user_id)
