from __future__ import annotations

import asyncio

import pytest

from src.client.manager import ClientManager, UserClient


class DummyClient:
    def __init__(self, user_id: int, event: asyncio.Event) -> None:
        self.user_id = user_id
        self._event = event

    async def start_listening(self) -> None:
        await self._event.wait()

    async def stop(self) -> None:
        self._event.set()


@pytest.mark.asyncio
async def test_client_manager_running_count():
    manager = ClientManager()
    event = asyncio.Event()
    client = DummyClient(user_id=1, event=event)
    manager.add_client(client)
    manager._schedule_mark_inactive = lambda _user_id: None  # type: ignore[assignment]

    await manager.start_client(1)
    assert manager.running_count() == 1
    assert manager.registered_count() == 1

    event.set()
    await asyncio.sleep(0.01)
    assert manager.running_count() == 0


@pytest.mark.asyncio
async def test_userclient_connect_failure():
    client = UserClient(user_id=2, api_id=123, api_hash="hash")

    class DummyClient:
        async def connect(self):
            raise RuntimeError("boom")

        async def is_user_authorized(self):
            return True

    client._client = DummyClient()
    assert await client.connect() is False


def test_userclient_session_string():
    client = UserClient(user_id=3, api_id=123, api_hash="hash")
    session_string = client.get_session_string()
    assert isinstance(session_string, str)


@pytest.mark.asyncio
async def test_userclient_start_listening():
    client = UserClient(user_id=4, api_id=123, api_hash="hash")
    event = asyncio.Event()

    class DummyTelethon:
        def __init__(self) -> None:
            self.handler = None

        def on(self, _event):
            def decorator(func):
                self.handler = func
                return func

            return decorator

        async def run_until_disconnected(self):
            await event.wait()

        async def disconnect(self):
            event.set()

    client._client = DummyTelethon()

    async def message_handler(_event):
        return None

    client.set_message_handler(message_handler)
    task = asyncio.create_task(client.start_listening())
    await asyncio.sleep(0)
    assert client._running is True
    event.set()
    await task
    assert client._running is False


@pytest.mark.asyncio
async def test_userclient_stop():
    client = UserClient(user_id=5, api_id=123, api_hash="hash")

    class DummyTelethon:
        def __init__(self) -> None:
            self.disconnected = False

        async def disconnect(self):
            self.disconnected = True

    dummy = DummyTelethon()
    client._client = dummy
    await client.stop()
    assert dummy.disconnected is True
