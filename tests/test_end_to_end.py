from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select


class DummySender:
    def __init__(self) -> None:
        self.bot = False
        self.first_name = "Alice"
        self.last_name = None
        self.username = "alice"


class DummyChat:
    def __init__(self) -> None:
        self.title = "Test Chat"


class DummyEvent:
    def __init__(self) -> None:
        self.raw_text = "hello"
        self.chat_id = 123
        self.sender_id = 123
        self.is_group = False
        self.is_channel = False
        self.is_private = True
        self._sender = DummySender()
        self._chat = DummyChat()
        self.responses: list[str] = []

    async def get_sender(self):
        return self._sender

    async def get_chat(self):
        return self._chat

    async def respond(self, text: str) -> None:
        self.responses.append(text)


@pytest.mark.asyncio
async def test_end_to_end_reply_flow(db_env, monkeypatch):
    handlers = db_env["handlers"]
    db = db_env["db"]

    async with db.async_session() as session:
        user = db.User(telegram_id=9001, is_active=True)
        session.add(user)
        await session.flush()
        session.add(db.UserSettings(user_id=user.id, ai_enabled=True, reply_delay_seconds=0))
        session.add(db.UserCredential(user_id=user.id, is_logged_in=True))
        await session.commit()

    async def fake_generate_reply(**_kwargs):
        return "auto-reply"

    monkeypatch.setattr(handlers, "generate_reply", fake_generate_reply)

    event = DummyEvent()
    await handlers._handle_incoming_message(9001, event)
    await asyncio.sleep(0.05)

    assert event.responses == ["auto-reply"]

    async with db.async_session() as session:
        result = await session.execute(
            select(db.MessageLog).where(db.MessageLog.user_id == user.id)
        )
        log = result.scalar_one()
        assert log.status == "sent"
        assert log.ai_reply == "auto-reply"
