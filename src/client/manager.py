"""Telethon 客户端管理器 - 管理用户账号的消息监听"""

import asyncio
import logging
from typing import Callable, Awaitable
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

logger = logging.getLogger(__name__)


class UserClient:
    """单个用户的 Telethon 客户端"""

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
        """获取当前 session 字符串用于持久化"""
        return self._session.save()

    async def connect(self) -> bool:
        """连接客户端"""
        try:
            await self.client.connect()
            return await self.client.is_user_authorized()
        except Exception:
            logger.exception("用户 %s 连接失败", self.user_id)
            return False

    async def send_code(self, phone: str) -> str:
        """发送验证码"""
        await self.client.connect()
        result = await self.client.send_code_request(phone)
        return result.phone_code_hash

    async def sign_in(
        self, phone: str, code: str, phone_code_hash: str, password: str | None = None
    ) -> tuple[bool, str]:
        """登录验证"""
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
        """设置消息处理器"""
        self._message_handler = handler

    async def start_listening(self):
        """开始监听消息"""
        if not self._message_handler:
            raise ValueError("请先设置消息处理器")

        if not self._handler_registered:
            @self.client.on(events.NewMessage(incoming=True))
            async def handler(event):
                if self._message_handler:
                    await self._message_handler(event)

            self._handler_registered = True

        self._running = True
        logger.info(f"用户 {self.user_id} 开始监听消息")
        try:
            await self.client.run_until_disconnected()
        finally:
            self._running = False
            logger.info(f"用户 {self.user_id} 监听已停止")

    async def stop(self):
        """停止客户端"""
        self._running = False
        if self._client:
            await self._client.disconnect()
            logger.info(f"用户 {self.user_id} 已断开连接")


class ClientManager:
    """管理所有用户客户端"""

    def __init__(self):
        self._clients: dict[int, UserClient] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    def get_client(self, user_id: int) -> UserClient | None:
        return self._clients.get(user_id)

    def add_client(self, client: UserClient):
        self._clients[client.user_id] = client

    def remove_client(self, user_id: int):
        if user_id in self._clients:
            del self._clients[user_id]
        if user_id in self._tasks:
            self._tasks[user_id].cancel()
            del self._tasks[user_id]

    def is_running(self, user_id: int) -> bool:
        task = self._tasks.get(user_id)
        return bool(task and not task.done())

    def running_count(self) -> int:
        """正在运行的客户端数量"""
        return sum(1 for task in self._tasks.values() if not task.done())

    def registered_count(self) -> int:
        """已注册的客户端数量"""
        return len(self._clients)

    async def start_client(self, user_id: int):
        """启动用户客户端监听"""
        client = self._clients.get(user_id)
        if not client:
            return

        if self.is_running(user_id):
            return

        task = asyncio.create_task(client.start_listening())
        self._tasks[user_id] = task
        task.add_done_callback(lambda t: self._handle_task_done(user_id, t))

    def _handle_task_done(self, user_id: int, task: asyncio.Task):
        if self._tasks.get(user_id) is task:
            del self._tasks[user_id]
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("用户 %s 监听任务异常退出", user_id)
        finally:
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
        """停止用户客户端"""
        client = self._clients.get(user_id)
        if client:
            await client.stop()
        if user_id in self._tasks:
            self._tasks[user_id].cancel()
            del self._tasks[user_id]

    async def stop_all(self):
        """停止所有客户端"""
        for user_id in list(self._clients.keys()):
            await self.stop_client(user_id)


# 全局实例
client_manager = ClientManager()
