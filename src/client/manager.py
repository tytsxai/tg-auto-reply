"""Telethon 客户端管理器 - 管理用户账号的消息监听"""

import asyncio
import logging
import os
from typing import Callable, Awaitable
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

logger = logging.getLogger(__name__)


class UserClient:
    """单个用户的 Telethon 客户端封装。

    负责管理单个用户的 Telegram 客户端连接、登录验证和消息监听。
    使用 StringSession 实现会话持久化，支持断线重连。

    Attributes:
        user_id: Telegram 用户 ID
        api_id: Telegram API ID
        api_hash: Telegram API Hash
    """

    def __init__(self, user_id: int, api_id: int, api_hash: str, session_string: str = ""):
        self.user_id = user_id
        self.api_id = api_id
        self.api_hash = api_hash
        self._session = StringSession(session_string)
        self._client: TelegramClient | None = None
        self._message_handler: Callable | None = None
        self._running = False
        self._handler_registered = False

    @property
    def client(self) -> TelegramClient:
        if not self._client:
            self._client = TelegramClient(self._session, self.api_id, self.api_hash)
        return self._client

    def get_session_string(self) -> str:
        """获取当前 session 字符串用于持久化存储。"""
        return self._session.save()

    async def connect(self) -> bool:
        """连接到 Telegram 服务器并检查授权状态。

        Returns:
            bool: True 表示已授权，False 表示需要登录

        Raises:
            Exception: 网络异常或 Telegram 服务不可达
        """
        await self.client.connect()
        return await self.client.is_user_authorized()

    async def send_code(self, phone: str) -> str:
        """发送登录验证码到指定手机号。

        Args:
            phone: 手机号（含国家代码，如 +8613800138000）

        Returns:
            str: phone_code_hash，用于后续验证
        """
        await self.client.connect()
        result = await self.client.send_code_request(phone)
        return result.phone_code_hash

    async def sign_in(
        self, phone: str, code: str, phone_code_hash: str, password: str | None = None
    ) -> tuple[bool, str]:
        """执行登录验证。

        Args:
            phone: 手机号
            code: 验证码（两步验证时可为空）
            phone_code_hash: send_code 返回的 hash
            password: 两步验证密码（可选）

        Returns:
            tuple[bool, str]: (是否成功, 消息)
        """
        try:
            await self.client.connect()
            if password:
                await self.client.sign_in(password=password)
                return True, "登录成功"
            if not code:
                return False, "验证码不能为空"
            await self.client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            return True, "登录成功"
        except SessionPasswordNeededError:
            return False, "需要两步验证密码"
        except Exception as e:
            return False, str(e)

    def set_message_handler(self, handler: Callable[[events.NewMessage.Event], Awaitable[None]]):
        """设置消息处理回调函数。

        Args:
            handler: 异步回调函数，接收 NewMessage 事件
        """
        self._message_handler = handler

    async def start_listening(self):
        """开始监听新消息，阻塞直到断开连接。

        Raises:
            ValueError: 未设置消息处理器
        """
        if not self._message_handler:
            raise ValueError("请先设置消息处理器")

        if not self._handler_registered:
            @self.client.on(events.NewMessage(incoming=True))
            async def handler(event):
                if self._message_handler:
                    await self._message_handler(event)

            self._handler_registered = True

        self._running = True
        reconnect_initial = float(os.getenv("CLIENT_RECONNECT_INITIAL_SECONDS", "1"))
        reconnect_max = float(os.getenv("CLIENT_RECONNECT_MAX_SECONDS", "30"))
        # CLIENT_RECONNECT_MAX_ATTEMPTS=0 表示无限重连（默认），>0 为最大尝试次数
        reconnect_max_attempts = int(os.getenv("CLIENT_RECONNECT_MAX_ATTEMPTS", "0"))
        if reconnect_initial <= 0:
            reconnect_initial = 1.0
        if reconnect_max < reconnect_initial:
            reconnect_max = reconnect_initial
        backoff = reconnect_initial
        attempt = 0

        try:
            while self._running:
                try:
                    await self.client.connect()
                    if not await self.client.is_user_authorized():
                        logger.warning("用户 %s 授权失效，停止监听", self.user_id)
                        await self.client.disconnect()
                        break
                    logger.info("用户 %s 开始监听消息", self.user_id)
                    backoff = reconnect_initial
                    attempt = 0  # 成功连接后重置计数
                    await self.client.run_until_disconnected()
                except asyncio.CancelledError:
                    self._running = False
                    raise
                except Exception:
                    attempt += 1
                    logger.exception("用户 %s 监听异常（第 %d 次）", self.user_id, attempt)
                    try:
                        await self.client.disconnect()
                    except Exception:
                        logger.debug("用户 %s 断线清理失败", self.user_id, exc_info=True)

                if not self._running:
                    break
                if reconnect_max_attempts > 0 and attempt >= reconnect_max_attempts:
                    logger.error(
                        "用户 %s 重连次数已达上限 %d，停止监听",
                        self.user_id,
                        reconnect_max_attempts,
                    )
                    self._running = False
                    break
                if backoff > 0:
                    logger.warning("用户 %s 断线，%s 秒后重连", self.user_id, backoff)
                    await asyncio.sleep(backoff)
                backoff = min(backoff * 2, reconnect_max)
        finally:
            self._running = False
            logger.info("用户 %s 监听已停止", self.user_id)

    async def stop(self):
        """停止客户端并断开连接。"""
        self._running = False
        if self._client:
            await self._client.disconnect()
            logger.info(f"用户 {self.user_id} 已断开连接")


class ClientManager:
    """全局客户端管理器，管理所有用户的 Telethon 客户端实例。

    负责客户端的生命周期管理，包括注册、启动、停止和状态查询。
    维护客户端字典和异步任务字典，支持并发运行多个用户客户端。
    """

    def __init__(self):
        self._clients: dict[int, UserClient] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    def get_client(self, user_id: int) -> UserClient | None:
        """获取指定用户的客户端实例。"""
        return self._clients.get(user_id)

    def add_client(self, client: UserClient):
        """注册客户端到管理器。"""
        self._clients[client.user_id] = client

    def remove_client(self, user_id: int):
        """移除客户端并取消其监听任务。"""
        if user_id in self._clients:
            del self._clients[user_id]
        if user_id in self._tasks:
            self._tasks[user_id].cancel()
            del self._tasks[user_id]

    def is_running(self, user_id: int) -> bool:
        """检查指定用户的客户端是否正在运行。"""
        task = self._tasks.get(user_id)
        return bool(task and not task.done())

    def running_count(self) -> int:
        """返回正在运行的客户端数量。"""
        return sum(1 for task in self._tasks.values() if not task.done())

    def registered_count(self) -> int:
        """返回已注册的客户端数量。"""
        return len(self._clients)

    async def start_client(self, user_id: int):
        """启动指定用户的客户端监听任务。"""
        client = self._clients.get(user_id)
        if not client:
            return

        if self.is_running(user_id):
            return

        task = asyncio.create_task(client.start_listening())
        self._tasks[user_id] = task
        task.add_done_callback(lambda t: self._handle_task_done(user_id, t))

    def _handle_task_done(self, user_id: int, task: asyncio.Task):
        is_current = self._tasks.get(user_id) is task
        if is_current:
            del self._tasks[user_id]
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("用户 %s 监听任务异常退出", user_id)
        finally:
            # 只在当前任务仍为现行监听时才标记 inactive，避免新任务被旧任务覆盖
            if is_current:
                self._schedule_mark_inactive(user_id)

    def _schedule_mark_inactive(self, user_id: int):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._mark_user_inactive(user_id))

    async def _mark_user_inactive(self, user_id: int):
        from sqlalchemy import select
        from src.db import async_session, User

        async with async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == user_id))
            user = result.scalar_one_or_none()
            if user and user.is_active:
                user.is_active = False
                await session.commit()

    async def stop_client(self, user_id: int):
        """停止指定用户的客户端并取消监听任务。"""
        client = self._clients.pop(user_id, None)  # 同步移除，防止内存泄漏
        if client:
            try:
                await client.stop()
            except Exception:
                logger.exception("停止用户 %s 客户端失败", user_id)

        task = self._tasks.pop(user_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stop_all(self):
        """停止所有客户端。"""
        for user_id in list(self._clients.keys()):
            await self.stop_client(user_id)


# 全局实例
client_manager = ClientManager()
