"""主程序入口"""

import os
import asyncio
import logging
import signal
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_handlers: list[logging.Handler] = [logging.StreamHandler()]
_log_file = os.getenv("LOG_FILE")
if _log_file:
    try:
        log_path = Path(_log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _handlers.append(logging.FileHandler(_log_file))
    except Exception as exc:
        print(f"⚠️ 无法创建日志文件 {_log_file}: {exc}")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)
_instance_lock_handle = None


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").strip().lower() in {"prod", "production"}


def _require_encryption_key() -> None:
    if not _is_production():
        return
    if os.getenv("ENCRYPTION_KEY"):
        return
    key_file = os.getenv("ENCRYPTION_KEY_FILE")
    if key_file and Path(key_file).exists():
        return
    raise ValueError(
        "生产环境必须设置 ENCRYPTION_KEY，或设置 ENCRYPTION_KEY_FILE 指向已有密钥文件。"
    )


def _log_startup_summary(allowed_ids: set[int] | None) -> None:
    env = os.getenv("ENVIRONMENT", "development")
    logger.info("环境: %s", env)

    if allowed_ids:
        logger.info("访问控制: 已启用 (%s 用户)", len(allowed_ids))
    elif _is_production():
        if _env_truthy("ALLOW_UNRESTRICTED_ACCESS"):
            logger.warning("访问控制: 未限制访问 (ALLOW_UNRESTRICTED_ACCESS=1)")
        else:
            logger.warning("访问控制未配置，生产环境存在被滥用风险")

    logger.info(
        "运行保护: concurrent=%s pending=%s cooldown=%s",
        os.getenv("MAX_CONCURRENT_REPLIES", "4"),
        os.getenv("MAX_PENDING_REPLY_TASKS", "200"),
        os.getenv("AUTO_REPLY_COOLDOWN_SECONDS", "15"),
    )
    logger.info(
        "运行保护(每用户): concurrent=%s pending=%s",
        os.getenv("MAX_CONCURRENT_REPLIES_PER_USER", "auto"),
        os.getenv("MAX_PENDING_REPLY_TASKS_PER_USER", "auto"),
    )
    logger.info(
        "日志: async=%s batch=%s interval=%s queue=%s",
        os.getenv("ENABLE_ASYNC_LOGGING", "1"),
        os.getenv("LOG_BATCH_SIZE", "20"),
        os.getenv("LOG_BATCH_INTERVAL", "1.0"),
        os.getenv("LOG_QUEUE_MAXSIZE", "1000"),
    )
    logger.info(
        "AI: model=%s base_url=%s timeout=%s retries=%s",
        os.getenv("AI_MODEL", "deepseek-ai/DeepSeek-V3.2"),
        os.getenv("OPENAI_BASE_URL", os.getenv("API_BASE_URL", "")),
        os.getenv("AI_TIMEOUT_SECONDS", "15"),
        os.getenv("AI_MAX_RETRIES", "1"),
    )


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    if not raw.strip().isdigit():
        raise ValueError(f"{name} 必须是整数")
    return int(raw)


def _env_truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _require_access_control(allowed_ids: set[int] | None) -> None:
    if not _is_production():
        return
    if allowed_ids:
        return
    if _env_truthy("ALLOW_UNRESTRICTED_ACCESS"):
        logger.warning("已显式允许未限制访问 (ALLOW_UNRESTRICTED_ACCESS=1)")
        return
    raise ValueError(
        "生产环境必须设置 ALLOWED_TELEGRAM_IDS，"
        "如需允许任意用户请设置 ALLOW_UNRESTRICTED_ACCESS=1。"
    )


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _require_healthcheck_token(health_host: str, health_token: str | None) -> None:
    if not _is_production():
        return
    if _is_loopback_host(health_host):
        return
    if health_token:
        return
    raise ValueError(
        "生产环境在非本机暴露健康检查时必须设置 HEALTHCHECK_TOKEN。"
    )


def _acquire_single_instance_lock() -> None:
    if not _is_production():
        return

    # Allow overriding lock location to support non-SQLite DB or shared volumes.
    lock_path_env = os.getenv("INSTANCE_LOCK_FILE", "").strip()
    lock_path = Path(lock_path_env) if lock_path_env else None

    if lock_path is None:
        db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")
        if db_url.startswith("sqlite"):
            try:
                from sqlalchemy.engine import make_url

                url = make_url(db_url)
                db_path = url.database
                if db_path and db_path != ":memory:":
                    lock_path = Path(str(db_path) + ".lock")
            except Exception:
                lock_path = None

    if lock_path is None:
        lock_path = Path("data/bot.lock")

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_file = lock_path.open("a+")
    except Exception as exc:
        raise RuntimeError(f"无法创建实例锁文件：{lock_path} ({exc})") from exc

    try:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        try:
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            lock_file.close()
            raise RuntimeError(
                f"检测到已有实例在运行（锁文件 {lock_path}）。"
            ) from exc
        except Exception:
            logger.warning("当前平台不支持文件锁，无法防止多实例运行。")
            lock_file.close()
            return
    except OSError as exc:
        lock_file.close()
        raise RuntimeError(
            f"检测到已有实例在运行（锁文件 {lock_path}）。"
        ) from exc

    global _instance_lock_handle
    _instance_lock_handle = lock_file


async def main():
    """主函数"""
    from telegram.ext import (
        Application,
        CommandHandler,
        ConversationHandler,
        MessageHandler,
        filters,
    )
    from sqlalchemy import update, text
    from src.db import init_db, async_session, User, verify_schema_version, dispose_engine
    from src.monitoring import HealthServer

    # 初始化数据库
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("请设置 BOT_TOKEN 环境变量")

    _require_encryption_key()
    _acquire_single_instance_lock()

    allow_without_ai = _env_truthy("ALLOW_START_WITHOUT_AI")
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")):
        msg = "未配置 OPENAI_API_KEY (或 API_KEY)"
        if allow_without_ai:
            logger.warning("%s，AI 回复将不可用。", msg)
        else:
            raise ValueError(f"{msg}，如需跳过请设置 ALLOW_START_WITHOUT_AI=1")

    from src.bot import (
        start,
        help_cmd,
        status,
        logs,
        start_hosting,
        stop_hosting,
        login_start,
        login_api_id,
        login_api_hash,
        login_phone,
        login_code,
        login_password,
        login_timeout,
        cancel,
        logout,
        settings,
        set_prompt,
        whitelist,
        blacklist,
        stats,
        about,
        start_log_worker,
        stop_log_worker,
        unauthorized,
        API_ID,
        API_HASH,
        PHONE,
        CODE,
        PASSWORD,
    )
    from src.bot.handlers import wait_for_reply_tasks
    from src.client import client_manager

    # 初始化数据库
    await init_db()
    logger.info("数据库初始化完成")

    if _env_truthy("ENABLE_STARTUP_HEALTHCHECKS", default=True):
        try:
            async with async_session() as session:
                await session.execute(text("SELECT 1"))
            logger.info("启动自检：数据库连接正常")
            await verify_schema_version()
            logger.info("启动自检：数据库 schema 版本正常")
        except Exception as exc:
            logger.exception("启动自检失败：数据库不可用")
            raise RuntimeError("数据库自检失败") from exc

    def _parse_allowed_ids():
        raw = os.getenv("ALLOWED_TELEGRAM_IDS", "").strip()
        if not raw:
            return None
        ids: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.lstrip("-").isdigit():
                raise ValueError("ALLOWED_TELEGRAM_IDS 必须是逗号分隔的数字 ID")
            ids.add(int(part))
        return ids or None

    allowed_ids = _parse_allowed_ids()
    _require_access_control(allowed_ids)
    user_filter = filters.User(user_id=list(allowed_ids)) if allowed_ids else None

    _log_startup_summary(allowed_ids)

    health_server = None
    health_port = _env_int("HEALTHCHECK_PORT")
    if health_port is None and _env_truthy("ENABLE_HTTP_HEALTHCHECK"):
        health_port = 8080
    if health_port is not None:
        health_host = os.getenv("HEALTHCHECK_HOST", "127.0.0.1")
        health_token = os.getenv("HEALTHCHECK_TOKEN")
        _require_healthcheck_token(health_host, health_token)
        health_server = HealthServer(host=health_host, port=health_port, token=health_token)

    app = Application.builder().token(token).build()

    # 登录对话处理器
    login_entry_filters = user_filter
    text_filters = filters.TEXT & ~filters.COMMAND
    if user_filter:
        text_filters &= user_filter

    login_handler = ConversationHandler(
        entry_points=[CommandHandler("login", login_start, filters=login_entry_filters)],
        states={
            API_ID: [MessageHandler(text_filters, login_api_id)],
            API_HASH: [MessageHandler(text_filters, login_api_hash)],
            PHONE: [MessageHandler(text_filters, login_phone)],
            CODE: [MessageHandler(text_filters, login_code)],
            PASSWORD: [MessageHandler(text_filters, login_password)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, login_timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=300,
    )

    # 注册处理器
    app.add_handler(CommandHandler("start", start, filters=user_filter))
    app.add_handler(CommandHandler("help", help_cmd, filters=user_filter))
    app.add_handler(CommandHandler("status", status, filters=user_filter))
    app.add_handler(CommandHandler("logs", logs, filters=user_filter))
    app.add_handler(CommandHandler("start_hosting", start_hosting, filters=user_filter))
    app.add_handler(CommandHandler("stop_hosting", stop_hosting, filters=user_filter))
    app.add_handler(CommandHandler("logout", logout, filters=user_filter))
    app.add_handler(CommandHandler("settings", settings, filters=user_filter))
    app.add_handler(CommandHandler("set_prompt", set_prompt, filters=user_filter))
    app.add_handler(CommandHandler("whitelist", whitelist, filters=user_filter))
    app.add_handler(CommandHandler("blacklist", blacklist, filters=user_filter))
    app.add_handler(CommandHandler("stats", stats, filters=user_filter))
    app.add_handler(CommandHandler("about", about, filters=user_filter))
    app.add_handler(login_handler)
    if user_filter:
        app.add_handler(MessageHandler(filters.COMMAND & ~user_filter, unauthorized))

    stop_event = asyncio.Event()

    def _handle_stop_signal(sig_name: str):
        logger.info("收到退出信号 %s，准备停止...", sig_name)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_stop_signal, sig.name)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())

    async def _mark_all_inactive():
        async with async_session() as session:
            await session.execute(update(User).values(is_active=False))
            await session.commit()

    logger.info("Bot 启动中...")
    async_logging_enabled = _env_truthy("ENABLE_ASYNC_LOGGING", default=True)
    try:
        async with app:
            await app.initialize()
            await app.start()
            if async_logging_enabled:
                await start_log_worker()
            await app.updater.start_polling()
            if health_server:
                await health_server.start()

            await stop_event.wait()
            logger.info("开始优雅停机...")
            shutdown_grace = _env_int("SHUTDOWN_GRACE_PERIOD_SECONDS", 10)
            if shutdown_grace is not None and shutdown_grace >= 0:
                remaining = await wait_for_reply_tasks(timeout=float(shutdown_grace))
                if remaining:
                    logger.warning("仍有 %s 个回复任务未完成，已取消", remaining)
            if health_server:
                await health_server.stop()
            if async_logging_enabled:
                await stop_log_worker()
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            await client_manager.stop_all()
            await _mark_all_inactive()
    finally:
        if health_server:
            try:
                await health_server.stop()
            except Exception:
                logger.debug("停止健康检查服务失败", exc_info=True)
        if async_logging_enabled:
            try:
                await stop_log_worker()
            except Exception:
                logger.debug("停止异步日志任务失败", exc_info=True)
        try:
            await client_manager.stop_all()
        except Exception:
            logger.debug("停止客户端管理器失败", exc_info=True)
        try:
            await _mark_all_inactive()
        except Exception:
            logger.debug("清理用户活动状态失败", exc_info=True)
        try:
            await dispose_engine()
        except Exception:
            logger.debug("释放数据库连接池失败", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
