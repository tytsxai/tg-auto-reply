from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.helpers import DummyContext, DummyMessage, DummyUpdate, DummyUser


async def _create_logged_in_user(db, telegram_id: int = 2001):
    async with db.async_session() as session:
        user = db.User(telegram_id=telegram_id, is_active=True)
        session.add(user)
        await session.flush()
        credential = db.UserCredential(user_id=user.id, is_logged_in=True)
        session.add(credential)
        await session.commit()
    return user


@pytest.mark.asyncio
async def test_settings_and_prompt(db_env):
    handlers = db_env["handlers"]
    db = db_env["db"]
    await _create_logged_in_user(db, telegram_id=3001)

    update = DummyUpdate(message=DummyMessage("/settings"), user=DummyUser(id=3001))
    context = DummyContext(args=[])

    await handlers.settings(update, context)
    assert any("当前设置" in text for text in update.message.reply_text_calls)

    context.args = ["ai", "off"]
    await handlers.settings(update, context)

    async with db.async_session() as session:
        result = await session.execute(
            select(db.UserSettings).join(db.User).where(db.User.telegram_id == 3001)
        )
        settings = result.scalar_one()
        assert settings.ai_enabled is False

    context.args = []
    update.message.reply_text_calls.clear()
    await handlers.set_prompt(update, context)
    assert "用法" in update.message.reply_text_calls[-1]

    context.args = ["你好"]
    await handlers.set_prompt(update, context)

    async with db.async_session() as session:
        result = await session.execute(
            select(db.UserSettings).join(db.User).where(db.User.telegram_id == 3001)
        )
        settings = result.scalar_one()
        assert settings.ai_prompt == "你好"


@pytest.mark.asyncio
async def test_set_prompt_rejects_too_long_prompt(db_env):
    handlers = db_env["handlers"]
    db = db_env["db"]
    await _create_logged_in_user(db, telegram_id=3002)

    long_prompt = "a" * 2001
    update = DummyUpdate(message=DummyMessage("/set_prompt"), user=DummyUser(id=3002))
    context = DummyContext(args=[long_prompt])

    await handlers.set_prompt(update, context)
    assert any("提示词过长" in text for text in update.message.reply_text_calls)

    async with db.async_session() as session:
        result = await session.execute(
            select(db.UserSettings).join(db.User).where(db.User.telegram_id == 3002)
        )
        settings = result.scalar_one_or_none()
        assert settings is None


@pytest.mark.asyncio
async def test_whitelist_flow(db_env):
    handlers = db_env["handlers"]
    db = db_env["db"]
    await _create_logged_in_user(db, telegram_id=4001)

    update = DummyUpdate(message=DummyMessage("/whitelist"), user=DummyUser(id=4001))
    context = DummyContext(args=[])

    await handlers.whitelist(update, context)
    assert any("列表为空" in text for text in update.message.reply_text_calls)

    context.args = ["add", "123"]
    await handlers.whitelist(update, context)

    async with db.async_session() as session:
        result = await session.execute(
            select(db.ContactList).join(db.User).where(db.User.telegram_id == 4001)
        )
        entry = result.scalar_one()
        assert entry.contact_id == 123

    context.args = ["list"]
    update.message.reply_text_calls.clear()
    await handlers.whitelist(update, context)
    assert any("显示最近" in text for text in update.message.reply_text_calls)

    context.args = ["remove", "123"]
    await handlers.whitelist(update, context)

    async with db.async_session() as session:
        result = await session.execute(
            select(db.ContactList).join(db.User).where(db.User.telegram_id == 4001)
        )
        assert result.scalar_one_or_none() is None

    context.args = ["clear"]
    await handlers.whitelist(update, context)


@pytest.mark.asyncio
async def test_logs_and_stats(db_env):
    handlers = db_env["handlers"]
    db = db_env["db"]
    user = await _create_logged_in_user(db, telegram_id=5001)

    async with db.async_session() as session:
        session.add_all(
            [
                db.MessageLog(
                    user_id=user.id,
                    chat_id=1,
                    original_message="a",
                    ai_reply="b",
                    status="sent",
                ),
                db.MessageLog(
                    user_id=user.id,
                    chat_id=1,
                    original_message="c",
                    ai_reply=None,
                    status="failed",
                ),
            ]
        )
        await session.commit()

    update = DummyUpdate(message=DummyMessage("/logs"), user=DummyUser(id=5001))
    context = DummyContext()
    await handlers.logs(update, context)
    assert any("原消息" in text for text in update.message.reply_text_calls)

    update.message.reply_text_calls.clear()
    await handlers.stats(update, context)
    assert any("总记录" in text for text in update.message.reply_text_calls)


@pytest.mark.asyncio
async def test_stats_requires_login(db_env):
    handlers = db_env["handlers"]
    update = DummyUpdate(message=DummyMessage("/stats"), user=DummyUser(id=5999))
    context = DummyContext()

    await handlers.stats(update, context)
    assert any("请先 /login 登录" in text for text in update.message.reply_text_calls)


@pytest.mark.asyncio
async def test_status_and_logout(db_env, monkeypatch):
    handlers = db_env["handlers"]
    db = db_env["db"]
    user = await _create_logged_in_user(db, telegram_id=5002)

    update = DummyUpdate(message=DummyMessage("/status"), user=DummyUser(id=5002))
    context = DummyContext()
    await handlers.status(update, context)
    assert any("登录状态" in text for text in update.message.reply_text_calls)

    update.message.reply_text_calls.clear()
    await handlers.logout(update, context)
    assert any("已退出" in text for text in update.message.reply_text_calls)

    async with db.async_session() as session:
        result = await session.execute(select(db.UserCredential).where(db.UserCredential.user_id == user.id))
        credential = result.scalar_one()
        assert credential.is_logged_in is False


@pytest.mark.asyncio
async def test_status_requires_login(db_env):
    handlers = db_env["handlers"]

    update = DummyUpdate(message=DummyMessage("/status"), user=DummyUser(id=9999))
    context = DummyContext()
    await handlers.status(update, context)
    assert any("当前未登录" in text for text in update.message.reply_text_calls)


@pytest.mark.asyncio
async def test_basic_commands(db_env):
    handlers = db_env["handlers"]
    update = DummyUpdate(message=DummyMessage("/start"), user=DummyUser(id=7001))
    context = DummyContext()

    await handlers.start(update, context)
    assert update.message.reply_text_calls

    update.message.reply_text_calls.clear()
    await handlers.help_cmd(update, context)
    assert update.message.reply_text_calls

    update.message.reply_text_calls.clear()
    await handlers.about(update, context)
    assert update.message.reply_text_calls

    update.message.reply_text_calls.clear()
    await handlers.cancel(update, context)
    assert update.message.reply_text_calls

    update.message.reply_text_calls.clear()
    await handlers.unauthorized(update, context)
    assert update.message.reply_text_calls
