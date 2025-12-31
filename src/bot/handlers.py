"""Bot 处理器"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from telethon.utils import get_display_name

from . import messages
from ..ai import generate_reply
from ..client import UserClient, client_manager
from ..db import async_session, User, UserCredential, UserSettings, MessageLog, ContactList
from ..utils import encryptor

logger = logging.getLogger(__name__)
API_ID, API_HASH, PHONE, CODE, PASSWORD = range(5)

MAX_CONCURRENT_REPLIES = max(1, int(os.getenv("MAX_CONCURRENT_REPLIES", "4")))
MAX_PENDING_REPLY_TASKS = max(1, int(os.getenv("MAX_PENDING_REPLY_TASKS", "200")))
AUTO_REPLY_COOLDOWN_SECONDS = max(0, int(os.getenv("AUTO_REPLY_COOLDOWN_SECONDS", "15")))
_reply_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REPLIES)
_pending_lock = asyncio.Lock()
_pending_reply_tasks = 0
_active_reply_tasks: set[asyncio.Task] = set()
_user_reply_tasks: dict[int, set[asyncio.Task]] = {}


def get_pending_reply_tasks() -> int:
    """获取当前等待中的回复任务数量。"""
    return _pending_reply_tasks


def get_reply_limits() -> tuple[int, int]:
    """获取并发/队列限制 (concurrent, pending)。"""
    return MAX_CONCURRENT_REPLIES, MAX_PENDING_REPLY_TASKS


def get_active_reply_task_count() -> int:
    """获取当前活跃的回复任务数量。"""
    return len(_active_reply_tasks)


async def wait_for_reply_tasks(timeout: float | None = None) -> int:
    """等待活跃回复任务完成，超时则取消。返回被取消的任务数量。"""
    if not _active_reply_tasks:
        return 0
    tasks = list(_active_reply_tasks)
    try:
        if timeout is None:
            await asyncio.gather(*tasks, return_exceptions=True)
            return 0
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
        return 0
    except asyncio.TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)


async def _reserve_reply_task() -> bool:
    global _pending_reply_tasks
    async with _pending_lock:
        if _pending_reply_tasks >= MAX_PENDING_REPLY_TASKS:
            return False
        _pending_reply_tasks += 1
        return True


async def _release_reply_task() -> None:
    global _pending_reply_tasks
    async with _pending_lock:
        _pending_reply_tasks = max(0, _pending_reply_tasks - 1)


def _track_reply_task(user_id: int, task: asyncio.Task) -> None:
    _active_reply_tasks.add(task)
    _user_reply_tasks.setdefault(user_id, set()).add(task)

    def _cleanup(done: asyncio.Task) -> None:
        _active_reply_tasks.discard(done)
        user_tasks = _user_reply_tasks.get(user_id)
        if user_tasks:
            user_tasks.discard(done)
            if not user_tasks:
                _user_reply_tasks.pop(user_id, None)

    task.add_done_callback(_cleanup)


async def _cancel_reply_tasks_for_user(user_id: int) -> int:
    tasks = list(_user_reply_tasks.get(user_id, set()))
    if not tasks:
        return 0
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return len(tasks)


async def _delete_sensitive_message(update: Update) -> None:
    message = update.effective_message
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        logger.debug("无法删除敏感消息", exc_info=True)


async def _ensure_user_settings(session: AsyncSession, user: User) -> UserSettings:
    if "settings" in user.__dict__ and user.settings:
        return user.settings

    if user.id:
        result = await session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )
        settings = result.scalar_one_or_none()
        if settings:
            user.settings = settings
            return settings

    settings = UserSettings(user_id=user.id)
    session.add(settings)
    await session.flush()
    return settings


async def _is_user_active(user_id: int) -> bool:
    async with async_session() as session:
        result = await session.execute(select(User.is_active).where(User.id == user_id))
        value = result.scalar_one_or_none()
        return bool(value)


def _clear_login_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ("api_id", "api_hash", "phone", "phone_code_hash", "client"):
        context.user_data.pop(key, None)


async def _abort_login(
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
    notify: str | None = None,
) -> None:
    client = context.user_data.get("client")
    if client:
        try:
            await client.stop()
        except Exception:
            logger.debug("登录取消时断开客户端失败", exc_info=True)
    _clear_login_context(context)
    if message and notify:
        await message.reply_text(notify)


def _format_sender_name(sender) -> str:
    if not sender:
        return "未知"
    parts = []
    first = getattr(sender, "first_name", None)
    last = getattr(sender, "last_name", None)
    username = getattr(sender, "username", None)
    if first:
        parts.append(first)
    if last:
        parts.append(last)
    name = " ".join(parts).strip()
    if not name and username:
        name = username
    return name or "未知"


def _normalize_bool_arg(value: str | None) -> bool | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text in {"on", "true", "1", "yes", "y", "开启", "开"}:
        return True
    if text in {"off", "false", "0", "no", "n", "关闭", "关"}:
        return False
    return None


def _format_contact_name(name: str | None, contact_id: int) -> str:
    display = (name or "").strip()
    return display if display else f"ID:{contact_id}"


async def _resolve_contact_target(
    update: Update, raw: str | None, telegram_id: int
) -> tuple[int | None, str | None, str | None]:
    """解析联系人/群组目标，返回 (contact_id, contact_name, error_message)."""
    message = update.effective_message
    if message:
        target_message = message.reply_to_message or message
        if target_message.forward_from:
            entity = target_message.forward_from
            return entity.id, _format_sender_name(entity), None
        if target_message.forward_from_chat:
            chat = target_message.forward_from_chat
            return chat.id, getattr(chat, "title", None) or getattr(chat, "username", None), None
        if target_message.sender_chat:
            chat = target_message.sender_chat
            return chat.id, getattr(chat, "title", None) or getattr(chat, "username", None), None
        if target_message.from_user:
            user = target_message.from_user
            return user.id, _format_sender_name(user), None

    if not raw:
        return None, None, "请提供目标（数字 ID 或转发消息）"

    raw = raw.strip()
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        return int(raw), None, None

    if raw.startswith("@"):
        client = client_manager.get_client(telegram_id)
        if not client:
            return None, None, "未找到可用的 Telegram 客户端，请先 /login"
        authorized = await client.connect()
        if not authorized:
            return None, None, "登录已失效，请 /login 重新登录"
        try:
            entity = await client.client.get_entity(raw)
        except Exception:
            if not client_manager.is_running(telegram_id):
                await client.stop()
            return None, None, "无法解析该用户名，请使用数字 ID 或转发消息"
        name = get_display_name(entity) if entity else None
        contact_id = getattr(entity, "id", None)
        if not client_manager.is_running(telegram_id):
            await client.stop()
        if contact_id is None:
            return None, None, "无法解析该用户名，请使用数字 ID 或转发消息"
        return int(contact_id), name, None

    return None, None, "无法解析目标，请使用数字 ID、@用户名或转发消息"


async def _log_message(
    user_id: int,
    chat_id: int | None,
    chat_title: str | None,
    sender_name: str | None,
    original_message: str | None,
    ai_reply: str | None,
    status: str,
) -> None:
    async with async_session() as session:
        session.add(
            MessageLog(
                user_id=user_id,
                chat_id=chat_id or 0,
                chat_title=chat_title,
                sender_name=sender_name,
                original_message=original_message,
                ai_reply=ai_reply,
                status=status,
            )
        )
        await session.commit()


async def _build_context(
    session: AsyncSession, user_id: int, chat_id: int
) -> list[dict[str, str]]:
    result = await session.execute(
        select(MessageLog)
        .where(MessageLog.user_id == user_id, MessageLog.chat_id == chat_id)
        .order_by(MessageLog.created_at.desc())
        .limit(5)
    )
    logs = list(reversed(result.scalars().all()))
    context: list[dict] = []
    for log in logs:
        if log.original_message:
            context.append({"role": "user", "content": log.original_message})
        if log.ai_reply:
            context.append({"role": "assistant", "content": log.ai_reply})
    return context


async def _should_reply(
    session: AsyncSession, user_id: int, settings: UserSettings, event: Any
) -> bool:
    if (event.is_group or event.is_channel) and not settings.auto_reply_groups:
        return False

    contact_id = None
    if event.is_private:
        contact_id = event.sender_id
    else:
        contact_id = event.chat_id or event.sender_id

    if contact_id is None:
        return False

    if settings.blacklist_enabled:
        blocked = await session.execute(
            select(ContactList.id).where(
                ContactList.user_id == user_id,
                ContactList.list_type == "blacklist",
                ContactList.contact_id == contact_id,
            )
        )
        if blocked.scalar_one_or_none():
            return False

    if settings.whitelist_only:
        allowed = await session.execute(
            select(ContactList.id).where(
                ContactList.user_id == user_id,
                ContactList.list_type == "whitelist",
                ContactList.contact_id == contact_id,
            )
        )
        if not allowed.scalar_one_or_none():
            return False

    return settings.ai_enabled


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    await message.reply_text(messages.WELCOME)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    await message.reply_text(messages.HELP)


async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message:
        await message.reply_text("🚫 未授权访问")


async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if not message:
        return ConversationHandler.END
    await message.reply_text(messages.LOGIN_START)
    return API_ID


async def login_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if not message or not message.text:
        return ConversationHandler.END
    text = message.text.strip()
    await _delete_sensitive_message(update)
    if not text.isdigit():
        await message.reply_text("❌ API ID 必须是纯数字")
        return API_ID
    context.user_data["api_id"] = int(text)
    await message.reply_text(messages.LOGIN_API_HASH)
    return API_HASH


async def login_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if not message or not message.text:
        return ConversationHandler.END
    text = message.text.strip()
    await _delete_sensitive_message(update)
    if len(text) != 32:
        await message.reply_text("❌ API Hash 应为32位")
        return API_HASH
    context.user_data["api_hash"] = text
    await message.reply_text(messages.LOGIN_PHONE)
    return PHONE


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if not message or not message.text:
        return ConversationHandler.END
    phone = message.text.strip()
    await _delete_sensitive_message(update)
    if not phone.startswith("+"):
        await message.reply_text("❌ 请包含国家代码如 +86")
        return PHONE
    context.user_data["phone"] = phone
    client = UserClient(
        user_id=update.effective_user.id,
        api_id=context.user_data["api_id"],
        api_hash=context.user_data["api_hash"],
    )
    try:
        hash = await client.send_code(phone)
        context.user_data["phone_code_hash"] = hash
        context.user_data["client"] = client
        await message.reply_text(messages.LOGIN_CODE)
        return CODE
    except Exception as e:
        await message.reply_text(f"❌ 失败：{e}")
        await client.stop()
        _clear_login_context(context)
        return ConversationHandler.END


async def login_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if not message or not message.text:
        return ConversationHandler.END
    code = message.text.strip()
    await _delete_sensitive_message(update)
    client = context.user_data.get("client")
    if not client:
        await message.reply_text("❌ 登录会话已过期，请重新 /login")
        _clear_login_context(context)
        return ConversationHandler.END
    success, msg = await client.sign_in(
        phone=context.user_data["phone"],
        code=code,
        phone_code_hash=context.user_data["phone_code_hash"],
    )
    if success:
        await _save_credentials(
            update.effective_user.id, context.user_data, client, update.effective_user
        )
        await message.reply_text(messages.LOGIN_SUCCESS)
        await client.stop()
        _clear_login_context(context)
        return ConversationHandler.END
    elif "两步验证" in msg:
        await message.reply_text(messages.LOGIN_2FA)
        return PASSWORD
    await message.reply_text(f"❌ {msg}")
    await client.stop()
    _clear_login_context(context)
    return ConversationHandler.END


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if not message or not message.text:
        return ConversationHandler.END
    client = context.user_data.get("client")
    if not client:
        await message.reply_text("❌ 登录会话已过期，请重新 /login")
        _clear_login_context(context)
        return ConversationHandler.END
    await _delete_sensitive_message(update)
    success, msg = await client.sign_in(
        phone=context.user_data["phone"],
        code="",
        phone_code_hash=context.user_data["phone_code_hash"],
        password=message.text.strip(),
    )
    if success:
        await _save_credentials(
            update.effective_user.id, context.user_data, client, update.effective_user
        )
        await message.reply_text(messages.LOGIN_SUCCESS)
    else:
        await message.reply_text(f"❌ {msg}")
    await client.stop()
    _clear_login_context(context)
    return ConversationHandler.END


async def _save_credentials(telegram_id: int, user_data: dict, client, telegram_user=None):
    from datetime import datetime
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.flush()
        if telegram_user:
            user.username = telegram_user.username
            user.first_name = telegram_user.first_name
        cred = await session.execute(
            select(UserCredential).where(UserCredential.user_id == user.id)
        )
        credential = cred.scalar_one_or_none()
        if not credential:
            credential = UserCredential(user_id=user.id)
            session.add(credential)
        await _ensure_user_settings(session, user)
        credential.api_id_encrypted = encryptor.encrypt(str(user_data["api_id"]))
        credential.api_hash_encrypted = encryptor.encrypt(user_data["api_hash"])
        credential.phone_encrypted = encryptor.encrypt(user_data["phone"])
        credential.session_string_encrypted = encryptor.encrypt(client.get_session_string())
        credential.is_logged_in = True
        credential.last_login = datetime.utcnow()
        if user.is_active:
            user.is_active = False
        await session.commit()
    existing_client = client_manager.get_client(telegram_id)
    if existing_client and existing_client is not client:
        await client_manager.stop_client(telegram_id)
    if user.id:
        await _cancel_reply_tasks_for_user(user.id)
    client_manager.add_client(client)


async def _handle_incoming_message(telegram_id: int, event: Any) -> None:
    try:
        text = (event.raw_text or "").strip()
        if not text:
            return

        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return
        chat = await event.get_chat()
        chat_id = event.chat_id or event.sender_id or 0
        sender_name = _format_sender_name(sender)
        chat_title = getattr(chat, "title", None)

        async with async_session() as session:
            result = await session.execute(
                select(User)
                .options(selectinload(User.settings))
                .where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user or not user.is_active:
                return
            settings = await _ensure_user_settings(session, user)
            await session.commit()

            should_reply = await _should_reply(session, user.id, settings, event)
            context = await _build_context(session, user.id, chat_id)

            cooldown_hit = False
            if should_reply and AUTO_REPLY_COOLDOWN_SECONDS > 0:
                last_sent = await session.execute(
                    select(MessageLog.created_at)
                    .where(
                        MessageLog.user_id == user.id,
                        MessageLog.chat_id == chat_id,
                        MessageLog.status == "sent",
                    )
                    .order_by(MessageLog.created_at.desc())
                    .limit(1)
                )
                last_time = last_sent.scalar_one_or_none()
                if last_time:
                    delta = datetime.utcnow() - last_time
                    if delta < timedelta(seconds=AUTO_REPLY_COOLDOWN_SECONDS):
                        cooldown_hit = True
                        should_reply = False

            settings_snapshot = {
                "ai_enabled": settings.ai_enabled,
                "ai_prompt": settings.ai_prompt,
                "reply_delay_seconds": settings.reply_delay_seconds,
            }
            user_id = user.id

        if not should_reply:
            await _log_message(
                user_id=user_id,
                chat_id=chat_id,
                chat_title=chat_title,
                sender_name=sender_name,
                original_message=text,
                ai_reply=None,
                status="cooldown" if cooldown_hit else "skipped",
            )
            return

        reserved = await _reserve_reply_task()
        if not reserved:
            logger.warning("回复队列已满，丢弃消息：user_id=%s chat_id=%s", user_id, chat_id)
            await _log_message(
                user_id=user_id,
                chat_id=chat_id,
                chat_title=chat_title,
                sender_name=sender_name,
                original_message=text,
                ai_reply=None,
                status="dropped",
            )
            return

        try:
            task = asyncio.create_task(
                _send_reply_task(
                    user_id=user_id,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    sender_name=sender_name,
                    original_message=text,
                    context=context,
                    settings_snapshot=settings_snapshot,
                    event=event,
                )
            )
            _track_reply_task(user_id, task)
        except Exception:
            await _release_reply_task()
            raise
    except Exception:
        logger.exception("处理来信失败：telegram_id=%s", telegram_id)


async def _send_reply_task(
    user_id: int,
    chat_id: int,
    chat_title: str | None,
    sender_name: str | None,
    original_message: str,
    context: list[dict],
    settings_snapshot: dict,
    event,
) -> None:
    reply = None
    status = "failed"
    try:
        async with _reply_semaphore:
            if not await _is_user_active(user_id):
                status = "skipped"
            else:
                reply = await generate_reply(
                    message=original_message,
                    sender_name=sender_name or "未知",
                    context=context,
                    system_prompt=settings_snapshot.get("ai_prompt"),
                )
                reply = (reply or "").strip()
                if not reply:
                    reply = "抱歉，我稍后回复您。"
                delay = max(0, int(settings_snapshot.get("reply_delay_seconds", 0)))
                if delay:
                    await asyncio.sleep(delay)
                if not await _is_user_active(user_id):
                    status = "skipped"
                else:
                    await event.respond(reply)
                    status = "sent"
    except asyncio.CancelledError:
        status = "cancelled"
    except Exception:
        logger.exception("AI 回复失败")
    finally:
        await _release_reply_task()

    await _log_message(
        user_id=user_id,
        chat_id=chat_id,
        chat_title=chat_title,
        sender_name=sender_name,
        original_message=original_message,
        ai_reply=reply,
        status=status,
    )


async def _handle_contact_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, list_type: str
) -> None:
    telegram_id = update.effective_user.id
    message = update.effective_message
    if not message:
        return
    args = context.args or []
    action = args[0].lower() if args else "list"

    async with async_session() as session:
        result = await session.execute(
            select(User)
            .options(selectinload(User.credentials))
            .where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if not user or not user.credentials or not user.credentials.is_logged_in:
            await message.reply_text("请先 /login 登录")
            return

        if action in {"list", "ls"}:
            total = await session.execute(
                select(func.count()).select_from(ContactList).where(
                    ContactList.user_id == user.id, ContactList.list_type == list_type
                )
            )
            entries = await session.execute(
                select(ContactList)
                .where(ContactList.user_id == user.id, ContactList.list_type == list_type)
                .order_by(ContactList.created_at.desc())
                .limit(20)
            )
            items = entries.scalars().all()
            if not items:
                await message.reply_text(
                    "📋 列表为空" if list_type == "whitelist" else "🚫 列表为空"
                )
                return
            lines = [
                f"{'📋' if list_type == 'whitelist' else '🚫'} 共 {total.scalar()} 项，显示最近 20 条："
            ]
            for item in items:
                name = _format_contact_name(item.contact_name, item.contact_id)
                lines.append(f"• {name} (ID: {item.contact_id})")
            await message.reply_text("\n".join(lines))
            return

        if action == "clear":
            await session.execute(
                delete(ContactList).where(
                    ContactList.user_id == user.id, ContactList.list_type == list_type
                )
            )
            await session.commit()
            await message.reply_text(
                "✅ 白名单已清空" if list_type == "whitelist" else "✅ 黑名单已清空"
            )
            return

        if action in {"add", "remove", "del", "delete"}:
            raw = " ".join(args[1:]).strip() if len(args) > 1 else None
            contact_id, contact_name, error = await _resolve_contact_target(
                update, raw, telegram_id
            )
            if error:
                await message.reply_text(f"❌ {error}")
                return

            if action == "add":
                existing = await session.execute(
                    select(ContactList).where(
                        ContactList.user_id == user.id,
                        ContactList.list_type == list_type,
                        ContactList.contact_id == contact_id,
                    )
                )
                if existing.scalar_one_or_none():
                    await message.reply_text("⚠️ 已存在于列表中")
                    return
                session.add(
                    ContactList(
                        user_id=user.id,
                        list_type=list_type,
                        contact_id=contact_id,
                        contact_name=contact_name,
                    )
                )
                await session.commit()
                await message.reply_text("✅ 已加入列表")
                return

            result = await session.execute(
                select(ContactList).where(
                    ContactList.user_id == user.id,
                    ContactList.list_type == list_type,
                    ContactList.contact_id == contact_id,
                )
            )
            entry = result.scalar_one_or_none()
            if not entry:
                await message.reply_text("⚠️ 列表中未找到该项")
                return
            await session.delete(entry)
            await session.commit()
            await message.reply_text("✅ 已移除")
            return

    cmd = "whitelist" if list_type == "whitelist" else "blacklist"
    await message.reply_text(
        f"用法：/{cmd} [list|add|remove|clear] <ID/@用户名/转发消息>\n"
        f"示例：/{cmd} add 123456"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message if update else None
    await _abort_login(context, message=message, notify="❌ 已取消")
    return ConversationHandler.END


async def login_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message if update else None
    await _abort_login(context, message=message, notify="⌛ 登录超时，请重新 /login")
    return ConversationHandler.END


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    message = update.effective_message
    if not message:
        return
    async with async_session() as session:
        result = await session.execute(
            select(User)
            .options(selectinload(User.credentials))
            .where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if not user or not user.credentials or not user.credentials.is_logged_in:
            await message.reply_text("📊 当前未登录，请先 /login")
            return
        hosting = "运行中" if user.is_active and client_manager.is_running(telegram_id) else "已停止"
    await message.reply_text(f"📊 登录状态：已登录\n🤖 托管状态：{hosting}")


async def start_hosting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    message = update.effective_message
    if not message:
        return
    async with async_session() as session:
        result = await session.execute(
            select(User).options(selectinload(User.settings)).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            await message.reply_text("❌ 请先 /login 登录")
            return
        cred = await session.execute(
            select(UserCredential).where(UserCredential.user_id == user.id)
        )
        credential = cred.scalar_one_or_none()
        if not credential or not credential.is_logged_in:
            await message.reply_text("❌ 登录信息缺失，请先 /login")
            return
        settings = await _ensure_user_settings(session, user)
        user.is_active = True
        await session.commit()

    try:
        api_id = int(encryptor.decrypt(credential.api_id_encrypted or ""))
        api_hash = encryptor.decrypt(credential.api_hash_encrypted or "")
        session_string = encryptor.decrypt(credential.session_string_encrypted or "")
    except Exception:
        logger.exception("无法解密凭证")
        async with async_session() as reset_session:
            result = await reset_session.execute(
                select(User)
                .options(selectinload(User.credentials))
                .where(User.telegram_id == telegram_id)
            )
            reset_user = result.scalar_one_or_none()
            if reset_user:
                reset_user.is_active = False
                if reset_user.credentials:
                    reset_user.credentials.is_logged_in = False
                await reset_session.commit()
        await message.reply_text("❌ 无法解密凭证，请 /login 重新登录")
        return

    client = client_manager.get_client(telegram_id)
    if not client:
        client = UserClient(
            user_id=telegram_id,
            api_id=api_id,
            api_hash=api_hash,
            session_string=session_string,
        )
        client_manager.add_client(client)

    client.set_message_handler(lambda event: _handle_incoming_message(telegram_id, event))
    authorized = await client.connect()
    if not authorized:
        async with async_session() as session:
            result = await session.execute(
                select(User).options(selectinload(User.credentials)).where(User.telegram_id == telegram_id)
            )
            stale_user = result.scalar_one_or_none()
            if stale_user:
                stale_user.is_active = False
                if stale_user.credentials:
                    stale_user.credentials.is_logged_in = False
                await session.commit()
        await message.reply_text("❌ 登录已失效，请 /login 重新登录")
        return

    if client_manager.is_running(telegram_id):
        await message.reply_text("✅ 托管已在运行中")
        return

    try:
        await client_manager.start_client(telegram_id)
    except Exception:
        logger.exception("启动托管失败")
        async with async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            fail_user = result.scalar_one_or_none()
            if fail_user:
                fail_user.is_active = False
                await session.commit()
        await message.reply_text("❌ 托管启动失败，请稍后重试")
        return

    ai_mode = "开启" if settings.ai_enabled else "关闭"
    await message.reply_text(
        messages.HOSTING_STARTED.format(delay=settings.reply_delay_seconds, ai_mode=ai_mode)
    )


async def stop_hosting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    message = update.effective_message
    if not message:
        return
    await client_manager.stop_client(telegram_id)
    user_id = None
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user:
            user.is_active = False
            user_id = user.id
            await session.commit()
    if user_id:
        await _cancel_reply_tasks_for_user(user_id)
    await message.reply_text(messages.HOSTING_STOPPED)


async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    message = update.effective_message
    if not message:
        return
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.reply_text("📝 暂无记录")
            return
        entries = await session.execute(
            select(MessageLog)
            .where(MessageLog.user_id == user.id)
            .order_by(MessageLog.created_at.desc())
            .limit(5)
        )
    logs_list = entries.scalars().all()
    if not logs_list:
        await message.reply_text("📝 暂无记录")
        return
    lines = []
    for entry in logs_list:
        lines.append(
            messages.LOG_ENTRY.format(
                time=entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                sender=entry.sender_name or "未知",
                message=entry.original_message or "",
                reply=entry.ai_reply or "",
                status=entry.status or "unknown",
            )
        )
    await message.reply_text("\n".join(lines))


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    message = update.effective_message
    if not message:
        return
    await client_manager.stop_client(telegram_id)
    client_manager.remove_client(telegram_id)
    user_id = None
    async with async_session() as session:
        result = await session.execute(
            select(User).options(selectinload(User.credentials)).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if user:
            user.is_active = False
            user_id = user.id
            if user.credentials:
                user.credentials.is_logged_in = False
                user.credentials.api_id_encrypted = None
                user.credentials.api_hash_encrypted = None
                user.credentials.phone_encrypted = None
                user.credentials.session_string_encrypted = None
            await session.commit()
    if user_id:
        await _cancel_reply_tasks_for_user(user_id)
    await message.reply_text("👋 已退出并清除本地凭证")


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    message = update.effective_message
    if not message:
        return
    async with async_session() as session:
        result = await session.execute(
            select(User)
            .options(selectinload(User.settings), selectinload(User.credentials))
            .where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if not user or not user.credentials or not user.credentials.is_logged_in:
            await message.reply_text("⚙️ 请先 /login 登录")
            return
        settings_obj = await _ensure_user_settings(session, user)

        if not context.args:
            prompt_preview = (settings_obj.ai_prompt or "").strip()
            if len(prompt_preview) > 80:
                prompt_preview = prompt_preview[:80] + "..."
            await message.reply_text(
                "\n".join(
                    [
                        "⚙️ 当前设置：",
                        f"• AI 回复：{'开启' if settings_obj.ai_enabled else '关闭'}",
                        f"• 回复延迟：{settings_obj.reply_delay_seconds} 秒",
                        f"• 回复群聊：{'开启' if settings_obj.auto_reply_groups else '关闭'}",
                        f"• 仅白名单：{'开启' if settings_obj.whitelist_only else '关闭'}",
                        f"• 黑名单过滤：{'开启' if settings_obj.blacklist_enabled else '关闭'}",
                        f"• 提示词：{prompt_preview or '未设置'}",
                        "",
                        "用法示例：",
                        "/settings ai on",
                        "/settings delay 5",
                        "/settings groups on",
                        "/settings whitelist_only on",
                        "/settings blacklist off",
                        "/set_prompt 你的提示词",
                    ]
                )
            )
            return

        key = context.args[0].lower()
        value = context.args[1] if len(context.args) > 1 else None

        if key in {"ai", "ai_enabled"}:
            enabled = _normalize_bool_arg(value)
            if enabled is None:
                await message.reply_text("用法：/settings ai on|off")
                return
            settings_obj.ai_enabled = enabled
        elif key in {"delay", "reply_delay", "reply_delay_seconds"}:
            if value is None or not value.lstrip("-").isdigit():
                await message.reply_text("用法：/settings delay <秒>")
                return
            delay = max(0, min(600, int(value)))
            settings_obj.reply_delay_seconds = delay
        elif key in {"groups", "group", "group_reply", "auto_reply_groups"}:
            enabled = _normalize_bool_arg(value)
            if enabled is None:
                await message.reply_text("用法：/settings groups on|off")
                return
            settings_obj.auto_reply_groups = enabled
        elif key in {"whitelist_only", "whitelist"}:
            enabled = _normalize_bool_arg(value)
            if enabled is None:
                await message.reply_text("用法：/settings whitelist_only on|off")
                return
            settings_obj.whitelist_only = enabled
        elif key in {"blacklist", "blacklist_enabled"}:
            enabled = _normalize_bool_arg(value)
            if enabled is None:
                await message.reply_text("用法：/settings blacklist on|off")
                return
            settings_obj.blacklist_enabled = enabled
        else:
            await message.reply_text("未知设置项。输入 /settings 查看可用项。")
            return

        await session.commit()
        await message.reply_text("✅ 设置已更新")


async def set_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    message = update.effective_message
    if not message:
        return
    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt:
        await message.reply_text("📝 用法：/set_prompt <提示词>")
        return
    async with async_session() as session:
        result = await session.execute(
            select(User)
            .options(selectinload(User.settings), selectinload(User.credentials))
            .where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if not user or not user.credentials or not user.credentials.is_logged_in:
            await message.reply_text("请先 /login 登录")
            return
        settings_obj = await _ensure_user_settings(session, user)
        settings_obj.ai_prompt = prompt
        await session.commit()
    await message.reply_text("✅ 提示词已更新")


async def whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle_contact_list(update, context, list_type="whitelist")


async def blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle_contact_list(update, context, list_type="blacklist")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    message = update.effective_message
    if not message:
        return
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.reply_text("📊 暂无统计")
            return
        total = await session.execute(
            select(func.count()).select_from(MessageLog).where(MessageLog.user_id == user.id)
        )
        sent = await session.execute(
            select(func.count())
            .select_from(MessageLog)
            .where(MessageLog.user_id == user.id, MessageLog.status == "sent")
        )
        failed = await session.execute(
            select(func.count())
            .select_from(MessageLog)
            .where(MessageLog.user_id == user.id, MessageLog.status == "failed")
        )
    await message.reply_text(
        f"📊 总记录：{total.scalar()}\n✅ 已发送：{sent.scalar()}\n❌ 失败：{failed.scalar()}"
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    await message.reply_text("🤖 消息托管助手 v1.0.5\nAI: DeepSeek-V3.2")
