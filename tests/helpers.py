from __future__ import annotations

from dataclasses import dataclass


class DummyMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.reply_text_calls: list[str] = []
        self.deleted = False
        self.reply_to_message = None
        self.forward_from = None
        self.forward_from_chat = None
        self.sender_chat = None
        self.from_user = None

    async def reply_text(self, text: str) -> None:
        self.reply_text_calls.append(text)

    async def delete(self) -> None:
        self.deleted = True


@dataclass
class DummyUser:
    id: int = 1001
    username: str = "tester"
    first_name: str = "Test"


class DummyUpdate:
    def __init__(self, message: DummyMessage | None = None, user: DummyUser | None = None) -> None:
        self.message = message
        self.effective_message = message
        self.effective_user = user or DummyUser()


class DummyContext:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []
        self.user_data: dict[str, object] = {}
