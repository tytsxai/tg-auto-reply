from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.helpers import DummyMessage, DummyUpdate
import asyncio


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

        now = datetime.utcnow()
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

    reserved = await handlers._reserve_reply_task()
    assert reserved is True
    assert handlers.get_pending_reply_tasks() == 1

    await handlers._release_reply_task()
    assert handlers.get_pending_reply_tasks() == 0


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
