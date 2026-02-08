from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

UTC = timezone.utc


class DummySender:
    def __init__(self) -> None:
        self.bot = False
        self.first_name = "Alice"
        self.last_name = None
        self.username = "alice"


class DummyEvent:
    def __init__(self) -> None:
        self.raw_text = "hello"
        self.chat_id = 321
        self.sender_id = 321
        self.is_group = False
        self.is_channel = False
        self.is_private = True
        self._sender = DummySender()
        self._chat = type("Chat", (), {"title": "Chat"})()
        self.responses: list[str] = []

    async def get_sender(self):
        return self._sender

    async def get_chat(self):
        return self._chat

    async def respond(self, text: str) -> None:
        self.responses.append(text)


async def _prepare_user(db, telegram_id: int = 9002):
    async with db.async_session() as session:
        user = db.User(telegram_id=telegram_id, is_active=True)
        session.add(user)
        await session.flush()
        session.add(db.UserSettings(user_id=user.id, ai_enabled=True, reply_delay_seconds=0))
        await session.commit()
    return user


@pytest.mark.asyncio
async def test_cooldown_skips_reply(db_env, monkeypatch):
    handlers = db_env["handlers"]
    db = db_env["db"]
    user = await _prepare_user(db, telegram_id=9002)

    monkeypatch.setattr(handlers, "AUTO_REPLY_COOLDOWN_SECONDS", 60)

    async with db.async_session() as session:
        session.add(
            db.MessageLog(
                user_id=user.id,
                chat_id=321,
                original_message="prev",
                ai_reply="prev",
                status="sent",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async def fake_generate_reply(**_kwargs):
        return "should-not-send"

    monkeypatch.setattr(handlers, "generate_reply", fake_generate_reply)

    event = DummyEvent()
    await handlers._handle_incoming_message(9002, event)

    async with db.async_session() as session:
        result = await session.execute(
            select(db.MessageLog)
            .where(db.MessageLog.user_id == user.id)
            .order_by(db.MessageLog.created_at.desc())
        )
        logs = result.scalars().all()
        assert logs[0].status == "cooldown"
    assert event.responses == []


@pytest.mark.asyncio
async def test_queue_full_drops_message(db_env, monkeypatch):
    handlers = db_env["handlers"]
    db = db_env["db"]
    user = await _prepare_user(db, telegram_id=9003)

    handlers._pending_reply_tasks = handlers.MAX_PENDING_REPLY_TASKS

    async def fake_generate_reply(**_kwargs):
        return "should-not-send"

    monkeypatch.setattr(handlers, "generate_reply", fake_generate_reply)

    event = DummyEvent()
    await handlers._handle_incoming_message(9003, event)

    async with db.async_session() as session:
        result = await session.execute(
            select(db.MessageLog)
            .where(db.MessageLog.user_id == user.id)
            .order_by(db.MessageLog.created_at.desc())
        )
        log = result.scalars().first()
        assert log.status == "dropped"
    assert event.responses == []
    handlers._pending_reply_tasks = 0
