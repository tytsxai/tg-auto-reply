from __future__ import annotations

import importlib
import types

import pytest

from src.ai import chat


class StubResponse:
    def __init__(self, content: str) -> None:
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]


class StubCompletions:
    def __init__(self, responses: list[object]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class StubClient:
    def __init__(self, responses: list[object]) -> None:
        self.chat = types.SimpleNamespace(completions=StubCompletions(responses))


@pytest.mark.asyncio
async def test_generate_reply_uses_system_prompt(monkeypatch):
    stub_client = StubClient([StubResponse("ok")])
    monkeypatch.setattr(chat, "get_client", lambda: stub_client)

    reply = await chat.generate_reply(
        message="hi", sender_name="Alex", context=[], system_prompt="prompt"
    )

    assert reply == "ok"
    assert stub_client.chat.completions.calls[0]["messages"][0]["content"] == "prompt"


@pytest.mark.asyncio
async def test_generate_reply_retries(monkeypatch):
    responses = [RuntimeError("boom"), StubResponse("retry-ok")]
    stub_client = StubClient(responses)
    monkeypatch.setattr(chat, "get_client", lambda: stub_client)
    monkeypatch.setenv("AI_MAX_RETRIES", "1")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "5")

    reply = await chat.generate_reply(message="hi", sender_name="Alex", context=[], system_prompt=None)

    assert reply == "retry-ok"
    assert len(stub_client.chat.completions.calls) == 2


def test_get_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    reloaded = importlib.reload(chat)

    with pytest.raises(ValueError):
        reloaded.get_client()


def test_get_client_with_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")

    reloaded = importlib.reload(chat)
    client = reloaded.get_client()
    assert "example.com" in str(client.base_url)
