from __future__ import annotations

import pytest

from tests.helpers import DummyContext, DummyMessage, DummyUpdate, DummyUser


class StubClient:
    def __init__(self, user_id: int, api_id: int, api_hash: str, session_string: str = "") -> None:
        self.user_id = user_id
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.connected = False
        self.handler = None

    def set_message_handler(self, handler) -> None:
        self.handler = handler

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def stop(self) -> None:
        self.connected = False


async def _create_user_with_credentials(db, encryptor, telegram_id: int = 8001):
    async with db.async_session() as session:
        user = db.User(telegram_id=telegram_id, is_active=False)
        session.add(user)
        await session.flush()
        session.add(
            db.UserCredential(
                user_id=user.id,
                is_logged_in=True,
                api_id_encrypted=encryptor.encrypt("123"),
                api_hash_encrypted=encryptor.encrypt("hash"),
                session_string_encrypted=encryptor.encrypt("session"),
            )
        )
        session.add(db.UserSettings(user_id=user.id, ai_enabled=True, reply_delay_seconds=0))
        await session.commit()
    return user


@pytest.mark.asyncio
async def test_start_and_stop_hosting(db_env, monkeypatch):
    handlers = db_env["handlers"]
    db = db_env["db"]

    await _create_user_with_credentials(db, handlers.encryptor, telegram_id=8001)

    stub_client = StubClient(user_id=8001, api_id=123, api_hash="hash")
    monkeypatch.setattr(handlers, "UserClient", lambda *args, **kwargs: stub_client)

    async def fake_start_client(_user_id: int) -> None:
        return None

    monkeypatch.setattr(handlers.client_manager, "start_client", fake_start_client)

    update = DummyUpdate(message=DummyMessage("/start_hosting"), user=DummyUser(id=8001))
    context = DummyContext()

    await handlers.start_hosting(update, context)
    assert any("托管已启动" in text for text in update.message.reply_text_calls)

    update.message.reply_text_calls.clear()
    await handlers.stop_hosting(update, context)
    assert any("托管已停止" in text for text in update.message.reply_text_calls)


@pytest.mark.asyncio
async def test_start_hosting_already_running(db_env, monkeypatch):
    handlers = db_env["handlers"]
    db = db_env["db"]

    await _create_user_with_credentials(db, handlers.encryptor, telegram_id=8002)

    stub_client = StubClient(user_id=8002, api_id=123, api_hash="hash")
    monkeypatch.setattr(handlers, "UserClient", lambda *args, **kwargs: stub_client)
    monkeypatch.setattr(handlers.client_manager, "is_running", lambda _user_id: True)

    update = DummyUpdate(message=DummyMessage("/start_hosting"), user=DummyUser(id=8002))
    context = DummyContext()

    await handlers.start_hosting(update, context)
    assert any("托管已在运行中" in text for text in update.message.reply_text_calls)


@pytest.mark.asyncio
async def test_start_hosting_login_invalid(db_env, monkeypatch):
    handlers = db_env["handlers"]
    db = db_env["db"]

    await _create_user_with_credentials(db, handlers.encryptor, telegram_id=8003)

    class BadClient(StubClient):
        async def connect(self) -> bool:
            return False

    bad_client = BadClient(user_id=8003, api_id=123, api_hash="hash")
    monkeypatch.setattr(handlers, "UserClient", lambda *args, **kwargs: bad_client)

    update = DummyUpdate(message=DummyMessage("/start_hosting"), user=DummyUser(id=8003))
    context = DummyContext()

    await handlers.start_hosting(update, context)
    assert any("登录已失效" in text for text in update.message.reply_text_calls)
