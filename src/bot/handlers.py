"""Bot 处理器 - Telegram Bot 命令和消息处理模块。

本模块负责处理所有 Bot 命令交互，包括：
- 用户登录流程（ConversationHandler）
- 托管控制（启动/停止自动回复）
- 设置管理（AI 开关、延迟、黑白名单）
- 消息回复任务调度和并发控制
"""

import asyncio
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
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

UTC = timezone.utc

MAX_CONCURRENT_REPLIES = max(1, int(os.getenv("MAX_CONCURRENT_REPLIES", "4")))
MAX_PENDING_REPLY_TASKS = max(1, int(os.getenv("MAX_PENDING_REPLY_TASKS", "200")))
AUTO_REPLY_COOLDOWN_SECONDS = max(0, int(os.getenv("AUTO_REPLY_COOLDOWN_SECONDS", "15")))
_default_per_user_pending = max(1, min(50, MAX_PENDING_REPLY_TASKS // 2))
_default_per_user_concurrent = max(1, MAX_CONCURRENT_REPLIES // 2)
MAX_PENDING_REPLY_TASKS_PER_USER = min(
    MAX_PENDING_REPLY_TASKS,
    max(1, int(os.getenv("MAX_PENDING_REPLY_TASKS_PER_USER", str(_default_per_user_pending)))),
)
MAX_CONCURRENT_REPLIES_PER_USER = min(
    MAX_CONCURRENT_REPLIES,
    max(
        1, int(os.getenv("MAX_CONCURRENT_REPLIES_PER_USER", str(_default_per_user_concurrent)))
    ),
)
_reply_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REPLIES)
_pending_lock = asyncio.Lock()
_pending_reply_tasks = 0
_pending_reply_tasks_by_user: dict[int, int] = {}
_active_reply_tasks: set[asyncio.Task] = set()
_user_reply_tasks: dict[int, set[asyncio.Task]] = {}
_user_semaphores: dict[int, asyncio.Semaphore] = {}

_LOG_QUEUE_MAXSIZE = max(10, int(os.getenv("LOG_QUEUE_MAXSIZE", "1000")))
_LOG_BATCH_SIZE = max(1, int(os.getenv("LOG_BATCH_SIZE", "20")))
_LOG_BATCH_INTERVAL = float(os.getenv("LOG_BATCH_INTERVAL", "1.0"))
_LOG_STOP = object()
_log_queue: asyncio.Queue["_LogRecord"] | None = None
_log_worker_task: asyncio.Task | None = None

_last_reply_lock = asyncio.Lock()
_last_scheduled_at: dict[tuple[int, int], datetime] = {}
_last_sent_at: dict[tuple[int, int], datetime] = {}
_inflight_ttl_default = max(3600, AUTO_REPLY_COOLDOWN_SECONDS * 10)
INFLIGHT_COOLDOWN_TTL_SECONDS = max(
    60, int(os.getenv("INFLIGHT_COOLDOWN_TTL_SECONDS", str(_inflight_ttl_default)))
)
_context_lock = asyncio.Lock()
_recent_context: dict[tuple[int, int], deque[tuple[datetime, str, str]]] = {}
_CONTEXT_MAX_MESSAGES = max(4, int(os.getenv("CONTEXT_MAX_MESSAGES", "10")))
_CONTEXT_CACHE_MAX_CHATS = max(100, int(os.getenv("CONTEXT_CACHE_MAX_CHATS", "1000")))
_CONTEXT_TTL_SECONDS = max(300, int(os.getenv("CONTEXT_TTL_SECONDS", "21600")))
_chat_tail_lock = asyncio.Lock()
_chat_task_tail: dict[tuple[int, int], asyncio.Future] = {}


@dataclass(frozen=True)
class _LogRecord:
    user_id: int
    chat_id: int
    chat_title: str | None
    sender_name: str | None
    original_message: str | None
    ai_reply: str | None
    status: str


def get_pending_reply_tasks() -> int:
    """获取当前等待中的回复任务数量。"""
    return _pending_reply_tasks


def get_reply_limits() -> tuple[int, int]:
    """获取并发/队列限制配置。

    Returns:
        tuple[int, int]: (最大并发数, 最大队列长度)
    """
    return MAX_CONCURRENT_REPLIES, MAX_PENDING_REPLY_TASKS


def get_active_reply_task_count() -> int:
    """获取当前活跃（正在执行）的回复任务数量。"""
    return len(_active_reply_tasks)


async def start_log_worker() -> None:
    """启动异步日志写入任务（若已启动则忽略）。"""
    global _log_queue, _log_worker_task
    if _log_worker_task and not _log_worker_task.done():
        return
    queue: asyncio.Queue[_LogRecord] = asyncio.Queue(maxsize=_LOG_QUEUE_MAXSIZE)
    _log_queue = queue
    _log_worker_task = asyncio.create_task(_log_worker(queue))


async def stop_log_worker() -> None:
    """停止日志写入任务并尽量刷新剩余队列。"""
    global _log_queue, _log_worker_task
    queue = _log_queue
    task = _log_worker_task
    _log_queue = None
    _log_worker_task = None
    if not queue or not task:
        return
    await queue.put(_LOG_STOP)
    await task


async def flush_log_queue(timeout: float | None = None) -> None:
    """等待日志队列清空（用于测试或停机前确保刷盘）。"""
    queue = _log_queue
    if not queue:
        return
    if timeout is None:
        await queue.join()
        return
    await asyncio.wait_for(queue.join(), timeout=timeout)


async def wait_for_reply_tasks(timeout: float | None = None) -> int:
    """等待所有活跃回复任务完成。

    Args:
        timeout: 超时时间（秒），None 表示无限等待

    Returns:
        int: 因超时被取消的任务数量
    """
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


def _get_user_semaphore(user_id: int) -> asyncio.Semaphore:
    """每用户并发限制，防止单用户挤占全局并发。"""
    sem = _user_semaphores.get(user_id)
    if not sem:
        sem = asyncio.Semaphore(MAX_CONCURRENT_REPLIES_PER_USER)
        _user_semaphores[user_id] = sem
    return sem


async def _reserve_reply_task(user_id: int) -> tuple[bool, str | None]:
    """预留待处理名额（全局 + 每用户），用于削峰和公平性。"""
    global _pending_reply_tasks
    async with _pending_lock:
        if _pending_reply_tasks >= MAX_PENDING_REPLY_TASKS:
            return False, "global"
        user_pending = _pending_reply_tasks_by_user.get(user_id, 0)
        if user_pending >= MAX_PENDING_REPLY_TASKS_PER_USER:
            return False, "per_user"
        _pending_reply_tasks += 1
        _pending_reply_tasks_by_user[user_id] = user_pending + 1
        return True, None


async def _release_reply_task(user_id: int) -> None:
    """释放待处理名额，并回收该用户的信号量。"""
    global _pending_reply_tasks
    cleanup = False
    async with _pending_lock:
        _pending_reply_tasks = max(0, _pending_reply_tasks - 1)
        user_pending = _pending_reply_tasks_by_user.get(user_id, 0) - 1
        if user_pending <= 0:
            _pending_reply_tasks_by_user.pop(user_id, None)
            cleanup = True
        else:
            _pending_reply_tasks_by_user[user_id] = user_pending
    if cleanup and user_id not in _user_reply_tasks:
        _user_semaphores.pop(user_id, None)


def _track_reply_task(user_id: int, task: asyncio.Task) -> None:
    """跟踪任务集合，用于停机/用户停用时批量取消。"""
    _active_reply_tasks.add(task)
    _user_reply_tasks.setdefault(user_id, set()).add(task)

    def _cleanup(done: asyncio.Task) -> None:
        _active_reply_tasks.discard(done)
        user_tasks = _user_reply_tasks.get(user_id)
        if user_tasks:
            user_tasks.discard(done)
            if not user_tasks:
                _user_reply_tasks.pop(user_id, None)
                if user_id not in _pending_reply_tasks_by_user:
                    _user_semaphores.pop(user_id, None)

    task.add_done_callback(_cleanup)


async def _cancel_reply_tasks_for_user(user_id: int) -> int:
    tasks = list(_user_reply_tasks.get(user_id, set()))
    if not tasks:
        return 0
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await _clear_user_chat_queue(user_id)
    return len(tasks)


def _prune_reply_timestamps(store: dict[tuple[int, int], datetime], cutoff: datetime) -> None:
    stale_keys: list[tuple[int, int]] = []
    for key, ts in store.items():
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff:
            stale_keys.append(key)
    for key in stale_keys:
        store.pop(key, None)


async def _check_schedule_cooldown(user_id: int, chat_id: int) -> bool:
    """并发场景下的内存冷却判断，避免短时间内重复排队。"""
    if AUTO_REPLY_COOLDOWN_SECONDS <= 0:
        return True
    now = datetime.now(UTC)
    key = (user_id, chat_id)
    async with _last_reply_lock:
        cutoff = now - timedelta(seconds=INFLIGHT_COOLDOWN_TTL_SECONDS)
        if len(_last_scheduled_at) > 1000:
            _prune_reply_timestamps(_last_scheduled_at, cutoff)
        if len(_last_sent_at) > 1000:
            _prune_reply_timestamps(_last_sent_at, cutoff)
        last_scheduled = _last_scheduled_at.get(key)
        if last_scheduled:
            if last_scheduled.tzinfo is None:
                last_scheduled = last_scheduled.replace(tzinfo=UTC)
            if now - last_scheduled < timedelta(seconds=AUTO_REPLY_COOLDOWN_SECONDS):
                return False
        last_sent = _last_sent_at.get(key)
        if last_sent:
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=UTC)
            if now - last_sent < timedelta(seconds=AUTO_REPLY_COOLDOWN_SECONDS):
                return False
        _last_scheduled_at[key] = now
    return True


async def _send_cooldown_hit(user_id: int, chat_id: int) -> bool:
    """发送前二次冷却校验，防止延迟后仍触发冷却窗口。"""
    if AUTO_REPLY_COOLDOWN_SECONDS <= 0:
        return False
    now = datetime.now(UTC)
    key = (user_id, chat_id)
    async with _last_reply_lock:
        cutoff = now - timedelta(seconds=INFLIGHT_COOLDOWN_TTL_SECONDS)
        if len(_last_sent_at) > 1000:
            _prune_reply_timestamps(_last_sent_at, cutoff)
        last_sent = _last_sent_at.get(key)
        if last_sent:
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=UTC)
            if now - last_sent < timedelta(seconds=AUTO_REPLY_COOLDOWN_SECONDS):
                return True
    return False


async def _mark_sent_now(user_id: int, chat_id: int) -> None:
    """记录发送时间，用于内存冷却与调度去抖。"""
    if AUTO_REPLY_COOLDOWN_SECONDS <= 0:
        return
    now = datetime.now(UTC)
    key = (user_id, chat_id)
    async with _last_reply_lock:
        _last_sent_at[key] = now
        _last_scheduled_at[key] = now


async def _clear_user_cooldown_state(user_id: int) -> None:
    """清理指定用户的内存冷却状态。"""
    async with _last_reply_lock:
        for key in list(_last_scheduled_at.keys()):
            if key[0] == user_id:
                _last_scheduled_at.pop(key, None)
        for key in list(_last_sent_at.keys()):
            if key[0] == user_id:
                _last_sent_at.pop(key, None)


async def _update_recent_context(
    user_id: int, chat_id: int, original_message: str | None, ai_reply: str | None
) -> None:
    """记录已发送的对话对（用户/助手），用于弥补异步日志落库延迟。"""
    if not original_message or not ai_reply:
        return
    now = datetime.now(UTC)
    key = (user_id, chat_id)
    async with _context_lock:
        history = _recent_context.get(key)
        if history is None:
            history = deque(maxlen=_CONTEXT_MAX_MESSAGES * 2)
            _recent_context[key] = history
        history.append((now, "user", original_message))
        history.append((now, "assistant", ai_reply))
        _prune_recent_context(now)


async def _get_recent_context(
    user_id: int, chat_id: int, since: datetime | None
) -> list[dict[str, str]]:
    """获取未落库的最新上下文片段（过滤已落库时间点之前的记录）。"""
    key = (user_id, chat_id)
    async with _context_lock:
        history = list(_recent_context.get(key, deque()))
    if not history:
        return []
    items: list[dict[str, str]] = []
    for ts, role, content in history:
        ts_value = ts
        if ts_value.tzinfo is None:
            ts_value = ts_value.replace(tzinfo=UTC)
        if since and ts_value <= since:
            continue
        items.append({"role": role, "content": content})
    return items


async def _clear_user_context(user_id: int) -> None:
    """清理指定用户的内存上下文缓存。"""
    async with _context_lock:
        for key in list(_recent_context.keys()):
            if key[0] == user_id:
                _recent_context.pop(key, None)


def _prune_recent_context(now: datetime) -> None:
    """按 TTL/最大对话数裁剪内存上下文，避免长期增长。"""
    if not _recent_context:
        return
    cutoff = now - timedelta(seconds=_CONTEXT_TTL_SECONDS)
    for key, history in list(_recent_context.items()):
        if not history:
            _recent_context.pop(key, None)
            continue
        last_ts = history[-1][0]
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=UTC)
        if last_ts < cutoff:
            _recent_context.pop(key, None)

    if len(_recent_context) <= _CONTEXT_CACHE_MAX_CHATS:
        return
    ordered: list[tuple[datetime, tuple[int, int]]] = []
    for key, history in _recent_context.items():
        if not history:
            continue
        ts = history[-1][0]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        ordered.append((ts, key))
    ordered.sort(key=lambda item: item[0])
    excess = len(_recent_context) - _CONTEXT_CACHE_MAX_CHATS
    for _ts, key in ordered[:excess]:
        _recent_context.pop(key, None)


async def _enter_chat_queue(user_id: int, chat_id: int) -> asyncio.Future:
    """同一对话串行化：等待前一条回复任务完成，避免乱序回复。"""
    key = (user_id, chat_id)
    loop = asyncio.get_running_loop()
    current = loop.create_future()
    async with _chat_tail_lock:
        prev = _chat_task_tail.get(key)
        _chat_task_tail[key] = current
    if prev:
        try:
            await prev
        except asyncio.CancelledError:
            await _exit_chat_queue(user_id, chat_id, current)
            raise
        except Exception:
            pass
    return current


async def _exit_chat_queue(user_id: int, chat_id: int, current: asyncio.Future) -> None:
    """释放对话队列的占位 future。"""
    key = (user_id, chat_id)
    if not current.done():
        current.set_result(None)
    async with _chat_tail_lock:
        if _chat_task_tail.get(key) is current:
            _chat_task_tail.pop(key, None)


async def _clear_user_chat_queue(user_id: int) -> None:
    """清理指定用户的对话队列，避免残留 future 阻塞后续回复。"""
    async with _chat_tail_lock:
        for key, future in list(_chat_task_tail.items()):
            if key[0] != user_id:
                continue
            if not future.done():
                future.set_result(None)
            _chat_task_tail.pop(key, None)


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

    # settings 依赖 user_id，调用方需确保 user 已 flush
    if user.id is None:
        raise RuntimeError("用户尚未写入数据库，请先 flush 后再创建 settings")

    settings = UserSettings(user_id=user.id)
    session.add(settings)
    await session.flush()
    return settings


async def _load_reply_settings(
    user_id: int, event: Any
) -> tuple[bool, dict[str, Any] | None]:
    """加载并校验当前回复所需的用户设置。

    返回 (是否允许回复, 设置快照或 None)。
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).options(selectinload(User.settings)).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            return False, None
        settings = await _ensure_user_settings(session, user)
        await session.commit()
        allowed = await _should_reply(session, user.id, settings, event)
        if not allowed:
            return False, None
        snapshot = {
            "ai_enabled": settings.ai_enabled,
            "ai_prompt": settings.ai_prompt,
            "reply_delay_seconds": settings.reply_delay_seconds,
        }
        return True, snapshot


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
    """解析联系人/群组目标，返回 (contact_id, contact_name, error_message)。

    仅在“回复/转发消息”场景从消息中解析目标，避免误把操作者自身当目标。
    """
    message = update.effective_message
    target_message = None
    if message:
        if message.reply_to_message:
            target_message = message.reply_to_message
        elif message.forward_from or message.forward_from_chat or message.sender_chat:
            # 仅在明确的回复/转发场景才从消息中解析目标，避免误把操作者自己当目标
            target_message = message

    if target_message:
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
            if not client_manager.is_running(telegram_id):
                await client.stop()
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
    """记录消息日志，优先走异步队列，队列满时回退同步写入。"""
    if status == "sent":
        await _update_recent_context(user_id, chat_id or 0, original_message, ai_reply)
    queue = _log_queue
    task = _log_worker_task
    record = _LogRecord(
        user_id=user_id,
        chat_id=chat_id or 0,
        chat_title=chat_title,
        sender_name=sender_name,
        original_message=original_message,
        ai_reply=ai_reply,
        status=status,
    )
    if queue and task and not task.done():
        try:
            queue.put_nowait(record)
            return
        except asyncio.QueueFull:
            logger.warning("日志队列已满，回退为同步写入")

    async with async_session() as session:
        session.add(
            MessageLog(
                user_id=record.user_id,
                chat_id=record.chat_id,
                chat_title=record.chat_title,
                sender_name=record.sender_name,
                original_message=record.original_message,
                ai_reply=record.ai_reply,
                status=record.status,
            )
        )
        await session.commit()


async def _flush_log_batch(queue: asyncio.Queue[_LogRecord], batch: list[_LogRecord]) -> None:
    if not batch:
        return
    try:
        async with async_session() as session:
            session.add_all(
                [
                    MessageLog(
                        user_id=record.user_id,
                        chat_id=record.chat_id,
                        chat_title=record.chat_title,
                        sender_name=record.sender_name,
                        original_message=record.original_message,
                        ai_reply=record.ai_reply,
                        status=record.status,
                    )
                    for record in batch
                ]
            )
            await session.commit()
    except Exception:
        logger.exception("批量写入日志失败")
    finally:
        for _ in batch:
            queue.task_done()
        batch.clear()


async def _log_worker(queue: asyncio.Queue[_LogRecord]) -> None:
    batch: list[_LogRecord] = []
    while True:
        item = None
        try:
            item = await asyncio.wait_for(queue.get(), timeout=_LOG_BATCH_INTERVAL)
        except asyncio.TimeoutError:
            pass

        if item is None:
            await _flush_log_batch(queue, batch)
            continue

        if item is _LOG_STOP:
            queue.task_done()
            await _flush_log_batch(queue, batch)
            while True:
                try:
                    extra = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if extra is _LOG_STOP:
                    queue.task_done()
                    continue
                batch.append(extra)
            await _flush_log_batch(queue, batch)
            break

        batch.append(item)
        if len(batch) >= _LOG_BATCH_SIZE:
            await _flush_log_batch(queue, batch)


async def _build_context(
    session: AsyncSession, user_id: int, chat_id: int
) -> list[dict[str, str]]:
    """构建 AI 对话上下文。

    从数据库获取最近 5 条消息记录作为上下文，并合并异步日志队列中的新回复，
    避免 DB 延迟导致上下文不连贯。

    Returns:
        list[dict]: OpenAI 格式的消息列表
    """
    result = await session.execute(
        select(MessageLog)
        .where(
            MessageLog.user_id == user_id,
            MessageLog.chat_id == chat_id,
            MessageLog.status == "sent",
        )
        .order_by(MessageLog.created_at.desc())
        .limit(5)
    )
    logs = list(reversed(result.scalars().all()))
    latest_db_time: datetime | None = None
    context: list[dict] = []
    for log in logs:
        if log.created_at:
            ts = log.created_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            latest_db_time = max(latest_db_time or ts, ts)
        if log.original_message:
            context.append({"role": "user", "content": log.original_message})
        if log.ai_reply:
            context.append({"role": "assistant", "content": log.ai_reply})
    recent_context = await _get_recent_context(user_id, chat_id, latest_db_time)
    if recent_context:
        context.extend(recent_context)
        context = context[-_CONTEXT_MAX_MESSAGES:]
    return context


async def _should_reply(
    session: AsyncSession, user_id: int, settings: UserSettings, event: Any
) -> bool:
    """判断是否应该回复该消息。

    检查群聊过滤、黑名单、白名单等规则。

    Returns:
        bool: True 表示应该回复
    """
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
        credential.last_login = datetime.now(UTC)
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
    """处理用户账号收到的新消息。

    核心消息处理流程：检查过滤规则、冷却时间、队列容量，
    符合条件则创建异步回复任务。

    Args:
        telegram_id: 用户的 Telegram ID
        event: Telethon NewMessage 事件
    """
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

            cooldown_hit = False
            if should_reply and AUTO_REPLY_COOLDOWN_SECONDS > 0:
                # DB 冷却用于跨重启一致性；内存冷却用于并发下的去抖。
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
                    # 数据库存储的是 naive datetime，需要添加 UTC 时区信息
                    if last_time.tzinfo is None:
                        last_time = last_time.replace(tzinfo=UTC)
                    delta = datetime.now(UTC) - last_time
                    if delta < timedelta(seconds=AUTO_REPLY_COOLDOWN_SECONDS):
                        cooldown_hit = True
                        should_reply = False

            context = None
            if should_reply:
                context = await _build_context(session, user.id, chat_id)

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

        reserved, reason = await _reserve_reply_task(user_id)
        if not reserved:
            if reason == "per_user":
                logger.warning(
                    "用户回复队列已满，丢弃消息：user_id=%s chat_id=%s", user_id, chat_id
                )
            else:
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

        if not await _check_schedule_cooldown(user_id, chat_id):
            await _release_reply_task(user_id)
            await _log_message(
                user_id=user_id,
                chat_id=chat_id,
                chat_title=chat_title,
                sender_name=sender_name,
                original_message=text,
                ai_reply=None,
                status="cooldown",
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
                    context=context or [],
                    settings_snapshot=settings_snapshot,
                    event=event,
                )
            )
            _track_reply_task(user_id, task)
        except Exception:
            await _release_reply_task(user_id)
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
    """执行 AI 回复任务。

    生成 AI 回复并发送，支持延迟发送和状态检查。
    任务完成后记录日志。
    """
    reply = None
    status = "failed"
    chat_guard = None
    try:
        # 同一对话串行化，避免多消息并发导致乱序回复。
        chat_guard = await _enter_chat_queue(user_id, chat_id)
        allowed, fresh_snapshot = await _load_reply_settings(user_id, event)
        effective_snapshot = fresh_snapshot or settings_snapshot
        if not allowed:
            status = "skipped"
        else:
            async with _reply_semaphore:
                async with _get_user_semaphore(user_id):
                    reply = await generate_reply(
                        message=original_message,
                        sender_name=sender_name or "未知",
                        context=context,
                        system_prompt=effective_snapshot.get("ai_prompt"),
                    )
            reply = (reply or "").strip()
            if not reply:
                reply = "抱歉，我稍后回复您。"
            delay = max(0, int(effective_snapshot.get("reply_delay_seconds", 0)))
            if delay:
                await asyncio.sleep(delay)
            allowed, _ = await _load_reply_settings(user_id, event)
            if not allowed:
                status = "skipped"
            elif await _send_cooldown_hit(user_id, chat_id):
                status = "cooldown"
            else:
                await event.respond(reply)
                await _mark_sent_now(user_id, chat_id)
                status = "sent"
    except asyncio.CancelledError:
        status = "cancelled"
    except Exception:
        logger.exception("AI 回复失败")
    finally:
        if chat_guard is not None:
            await _exit_chat_queue(user_id, chat_id, chat_guard)
        await _release_reply_task(user_id)

    try:
        await _log_message(
            user_id=user_id,
            chat_id=chat_id,
            chat_title=chat_title,
            sender_name=sender_name,
            original_message=original_message,
            ai_reply=reply,
            status=status,
        )
    except Exception:
        logger.exception("记录回复日志失败")


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
                try:
                    await session.commit()
                    await message.reply_text("✅ 已加入列表")
                except IntegrityError:
                    await session.rollback()
                    await message.reply_text("⚠️ 已存在于列表中")
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
        # 非运行态下的临时连接要及时断开，避免占用连接资源
        if not client_manager.is_running(telegram_id):
            await client.stop()
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
        async with async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            running_user = result.scalar_one_or_none()
            if running_user and not running_user.is_active:
                running_user.is_active = True
                await session.commit()
        await message.reply_text("✅ 托管已在运行中")
        return

    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        active_user = result.scalar_one_or_none()
        if active_user:
            active_user.is_active = True
            await session.commit()

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
        await _clear_user_cooldown_state(user_id)
        await _clear_user_context(user_id)
        await _clear_user_chat_queue(user_id)
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
        await _clear_user_cooldown_state(user_id)
        await _clear_user_context(user_id)
        await _clear_user_chat_queue(user_id)
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
    from src.version import __version__
    message = update.effective_message
    if not message:
        return
    await message.reply_text(f"🤖 消息托管助手 v{__version__}\nAI: DeepSeek-V3.2")
