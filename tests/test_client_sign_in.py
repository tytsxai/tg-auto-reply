from __future__ import annotations

import pytest
from telethon.errors import SessionPasswordNeededError

from src.client.manager import UserClient


class DummyTelethonClient:
    def __init__(self) -> None:
        self.sign_in_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def is_user_authorized(self) -> bool:
        return True

    async def sign_in(self, *args, **kwargs) -> None:
        self.sign_in_calls.append((args, kwargs))
        if kwargs.get("raise_password"):
            raise SessionPasswordNeededError(request=None)


@pytest.mark.asyncio
async def test_sign_in_with_password_path():
    client = UserClient(user_id=1, api_id=123, api_hash="hash")
    client._client = DummyTelethonClient()

    success, msg = await client.sign_in(
        phone="+1", code="12345", phone_code_hash="hash", password="pw"
    )

    assert success is True
    assert msg == "登录成功"
    assert client._client.sign_in_calls[-1][1] == {"password": "pw"}


@pytest.mark.asyncio
async def test_sign_in_requires_password():
    client = UserClient(user_id=1, api_id=123, api_hash="hash")
    dummy = DummyTelethonClient()
    client._client = dummy

    async def sign_in_raise(*args, **kwargs):
        raise SessionPasswordNeededError(request=None)

    dummy.sign_in = sign_in_raise  # type: ignore[assignment]

    success, msg = await client.sign_in(phone="+1", code="12345", phone_code_hash="hash")

    assert success is False
    assert "两步验证" in msg


@pytest.mark.asyncio
async def test_sign_in_requires_code():
    client = UserClient(user_id=1, api_id=123, api_hash="hash")
    client._client = DummyTelethonClient()

    success, msg = await client.sign_in(phone="+1", code="", phone_code_hash="hash")

    assert success is False
    assert msg == "验证码不能为空"
