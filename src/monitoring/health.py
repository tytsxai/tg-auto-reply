"""健康检查与监控端点。"""

from __future__ import annotations

import logging
from datetime import datetime

from aiohttp import web
from sqlalchemy import func, select, text

from src.bot import handlers as bot_handlers
from src.client import client_manager
from src.db import SCHEMA_VERSION, async_session, get_schema_version, User

logger = logging.getLogger(__name__)


class HealthServer:
    """提供 /healthz, /readyz, /metrics 的轻量 HTTP 服务。"""

    def __init__(self, host: str, port: int, token: str | None = None) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._started_at = datetime.utcnow()

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/healthz", self._handle_health)
        app.router.add_get("/readyz", self._handle_ready)
        app.router.add_get("/metrics", self._handle_metrics)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        logger.info("健康检查服务启动: http://%s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            logger.info("健康检查服务已停止")

    def _authorized(self, request: web.Request) -> bool:
        if not self._token:
            return True
        header = request.headers.get("X-Health-Token") or request.headers.get("Authorization")
        if not header:
            return False
        if header.lower().startswith("bearer "):
            token = header.split(" ", 1)[1].strip()
        else:
            token = header.strip()
        return token == self._token

    def _require_auth(self, request: web.Request) -> None:
        if not self._authorized(request):
            raise web.HTTPUnauthorized(text="unauthorized")

    async def _db_check(self) -> tuple[bool, int | None]:
        try:
            async with async_session() as session:
                await session.execute(text("SELECT 1"))
            version = await get_schema_version()
            return True, version
        except Exception:
            logger.debug("健康检查数据库检测失败", exc_info=True)
            return False, None

    async def _handle_health(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        uptime = int((datetime.utcnow() - self._started_at).total_seconds())
        return web.json_response({"status": "ok", "uptime_seconds": uptime})

    async def _handle_ready(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        db_ok, schema_version = await self._db_check()
        ready = db_ok and (schema_version == SCHEMA_VERSION)
        payload = {
            "status": "ok" if ready else "error",
            "db_ok": db_ok,
            "schema_version": schema_version,
            "expected_schema_version": SCHEMA_VERSION,
        }
        return web.json_response(payload, status=200 if ready else 503)

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        pending = bot_handlers.get_pending_reply_tasks()
        active = bot_handlers.get_active_reply_task_count()
        concurrent, pending_limit = bot_handlers.get_reply_limits()
        running_clients = client_manager.running_count()
        registered_clients = client_manager.registered_count()

        active_users = None
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(func.count()).select_from(User).where(User.is_active.is_(True))
                )
                active_users = int(result.scalar() or 0)
        except Exception:
            logger.debug("健康检查统计活跃用户失败", exc_info=True)

        lines = [
            "# HELP bot_pending_reply_tasks Pending reply tasks count.",
            "# TYPE bot_pending_reply_tasks gauge",
            f"bot_pending_reply_tasks {pending}",
            "# HELP bot_active_reply_tasks Active reply tasks count.",
            "# TYPE bot_active_reply_tasks gauge",
            f"bot_active_reply_tasks {active}",
            "# HELP bot_pending_reply_tasks_limit Max pending reply tasks.",
            "# TYPE bot_pending_reply_tasks_limit gauge",
            f"bot_pending_reply_tasks_limit {pending_limit}",
            "# HELP bot_concurrent_reply_limit Max concurrent replies.",
            "# TYPE bot_concurrent_reply_limit gauge",
            f"bot_concurrent_reply_limit {concurrent}",
            "# HELP bot_running_clients Running telethon clients.",
            "# TYPE bot_running_clients gauge",
            f"bot_running_clients {running_clients}",
            "# HELP bot_registered_clients Registered telethon clients.",
            "# TYPE bot_registered_clients gauge",
            f"bot_registered_clients {registered_clients}",
            "# HELP bot_schema_version Current database schema version.",
            "# TYPE bot_schema_version gauge",
            f"bot_schema_version {SCHEMA_VERSION}",
        ]

        if active_users is not None:
            lines.extend(
                [
                    "# HELP bot_active_users Active hosting users.",
                    "# TYPE bot_active_users gauge",
                    f"bot_active_users {active_users}",
                ]
            )

        text_body = "\n".join(lines) + "\n"
        return web.Response(text=text_body, content_type="text/plain")
