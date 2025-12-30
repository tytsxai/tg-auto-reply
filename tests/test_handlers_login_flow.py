from __future__ import annotations

import pytest
from telegram.ext import ConversationHandler

from tests.helpers import DummyContext, DummyMessage, DummyUpdate, DummyUser


class StubUserClient:
    def __init__(self, user_id: int, api_id: int, api_hash: str, session_string: str = "") -> None:
        self.user_id = user_id
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.stopped = False
        self.sign_in_response: tuple[bool, str] = (True, "登录成功")

    async def send_code(self, _phone: str) -> str:
        return "code-hash"

    async def sign_in(self, **_kwargs):
        return self.sign_in_response

    def get_session_string(self) -> str:
        return "session-string"

    async def stop(self) -> None:
        self.stopped = True

    async def connect(self) -> bool:
        return True

    @property
    def client(self):
        return self

    async def get_entity(self, _raw):
        return None


@pytest.mark.asyncio
async def test_login_validation_and_flow(db_env, monkeypatch):
    handlers = db_env["handlers"]

    update = DummyUpdate(message=DummyMessage("abc"), user=DummyUser(id=6001))
    context = DummyContext()

    state = await handlers.login_api_id(update, context)
    assert state == handlers.API_ID
    assert "API ID" in update.message.reply_text_calls[-1]

    update.message.text = "123"
    state = await handlers.login_api_id(update, context)
    assert state == handlers.API_HASH

    update.message.text = "short"
    state = await handlers.login_api_hash(update, context)
    assert state == handlers.API_HASH
    assert "API Hash" in update.message.reply_text_calls[-1]

    update.message.text = "0" * 32
    state = await handlers.login_api_hash(update, context)
    assert state == handlers.PHONE

    update.message.text = "12345"
    state = await handlers.login_phone(update, context)
    assert state == handlers.PHONE

    stub_client = StubUserClient(user_id=6001, api_id=123, api_hash="hash")
    monkeypatch.setattr(handlers, "UserClient", lambda *args, **kwargs: stub_client)

    update.message.text = "+1-555"
    state = await handlers.login_phone(update, context)
    assert state == handlers.CODE
    assert context.user_data["phone"] == "+1-555"
    assert context.user_data["client"] is stub_client

    update.message.text = "12345"
    state = await handlers.login_code(update, context)
    assert state == ConversationHandler.END


@pytest.mark.asyncio
async def test_login_code_requires_password(db_env):
    handlers = db_env["handlers"]

    update = DummyUpdate(message=DummyMessage("12345"), user=DummyUser(id=6002))
    context = DummyContext()
    context.user_data["client"] = StubUserClient(user_id=6002, api_id=123, api_hash="hash")
    context.user_data["client"].sign_in_response = (False, "需要两步验证密码")
    context.user_data["phone"] = "+1"
    context.user_data["phone_code_hash"] = "hash"
    context.user_data["api_id"] = 123
    context.user_data["api_hash"] = "hash"

    state = await handlers.login_code(update, context)
    assert state == handlers.PASSWORD


@pytest.mark.asyncio
async def test_login_code_missing_client(db_env):
    handlers = db_env["handlers"]

    update = DummyUpdate(message=DummyMessage("12345"), user=DummyUser(id=6003))
    context = DummyContext()

    state = await handlers.login_code(update, context)
    assert state == ConversationHandler.END
    assert "会话已过期" in update.message.reply_text_calls[-1]


@pytest.mark.asyncio
async def test_login_password_success(db_env):
    handlers = db_env["handlers"]

    update = DummyUpdate(message=DummyMessage("password"), user=DummyUser(id=6004))
    context = DummyContext()
    context.user_data["client"] = StubUserClient(user_id=6004, api_id=123, api_hash="hash")
    context.user_data["phone"] = "+1"
    context.user_data["phone_code_hash"] = "hash"
    context.user_data["api_id"] = 123
    context.user_data["api_hash"] = "hash"

    state = await handlers.login_password(update, context)
    assert state == ConversationHandler.END
