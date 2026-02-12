#!/usr/bin/env python3
"""生产就绪预检脚本。

目标：在上线前快速发现“现在不修、上线就会出问题”的硬缺口。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ==================== 结果模型 ====================


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str
    level: str = "ERROR"  # ERROR / WARN / INFO


# ==================== 工具函数 ====================


def _env_truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").strip().lower() in {"prod", "production"}


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _check_required(name: str, *, allow_empty: bool = False) -> CheckResult:
    value = os.getenv(name)
    if value is None:
        return CheckResult(name=name, ok=False, message=f"缺少环境变量 {name}")
    if not allow_empty and not value.strip():
        return CheckResult(name=name, ok=False, message=f"环境变量 {name} 为空")
    return CheckResult(name=name, ok=True, message="已配置")


def _check_numeric(name: str, caster: Callable[[str], object]) -> CheckResult:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return CheckResult(name=name, ok=True, message="未设置，使用默认值", level="INFO")
    try:
        caster(raw.strip())
    except Exception:
        return CheckResult(name=name, ok=False, message=f"{name} 不是合法数字: {raw!r}")
    return CheckResult(name=name, ok=True, message=f"值合法: {raw.strip()}")


def _is_valid_fernet_key(value: str) -> bool:
    try:
        from cryptography.fernet import Fernet

        Fernet(value.encode())
        return True
    except Exception:
        return False


def _check_encryption_key() -> CheckResult:
    env_key = os.getenv("ENCRYPTION_KEY", "").strip()
    if env_key:
        if _is_valid_fernet_key(env_key):
            return CheckResult(name="encryption", ok=True, message="ENCRYPTION_KEY 已配置且格式合法")
        return CheckResult(
            name="encryption",
            ok=False,
            message="ENCRYPTION_KEY 格式非法（需要 Fernet urlsafe-base64 32-byte key）",
        )

    key_file = os.getenv("ENCRYPTION_KEY_FILE", "data/encryption.key").strip() or "data/encryption.key"
    key_path = Path(key_file)
    if key_path.exists():
        try:
            file_key = key_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return CheckResult(
                name="encryption",
                ok=False,
                message=f"读取密钥文件失败: {exc}",
            )
        if not file_key:
            return CheckResult(name="encryption", ok=False, message=f"密钥文件为空: {key_path}")
        if not _is_valid_fernet_key(file_key):
            return CheckResult(
                name="encryption",
                ok=False,
                message=f"密钥文件内容格式非法: {key_path}",
            )
        return CheckResult(
            name="encryption",
            ok=True,
            message=f"使用 ENCRYPTION_KEY_FILE: {key_path}",
        )
    return CheckResult(
        name="encryption",
        ok=False,
        message=(
            "生产环境必须配置 ENCRYPTION_KEY，或让 ENCRYPTION_KEY_FILE 指向已存在的密钥文件"
        ),
    )


def _check_access_control() -> CheckResult:
    allowed_ids = os.getenv("ALLOWED_TELEGRAM_IDS", "").strip()
    if allowed_ids:
        parts = [part.strip() for part in allowed_ids.split(",") if part.strip()]
        if not parts:
            return CheckResult(name="access_control", ok=False, message="ALLOWED_TELEGRAM_IDS 为空")
        invalid = [part for part in parts if not part.lstrip("-").isdigit()]
        if invalid:
            return CheckResult(
                name="access_control",
                ok=False,
                message=f"ALLOWED_TELEGRAM_IDS 包含非法 ID: {', '.join(invalid)}",
            )
        return CheckResult(name="access_control", ok=True, message=f"ALLOWED_TELEGRAM_IDS 已配置 ({len(parts)} 项)")
    if _env_truthy("ALLOW_UNRESTRICTED_ACCESS"):
        return CheckResult(
            name="access_control",
            ok=True,
            message="允许未限制访问（ALLOW_UNRESTRICTED_ACCESS=1）",
            level="WARN",
        )
    return CheckResult(
        name="access_control",
        ok=False,
        message="生产环境必须设置 ALLOWED_TELEGRAM_IDS，或显式设置 ALLOW_UNRESTRICTED_ACCESS=1",
    )


def _check_health_token() -> CheckResult:
    enabled = bool(os.getenv("HEALTHCHECK_PORT", "").strip()) or _env_truthy(
        "ENABLE_HTTP_HEALTHCHECK"
    )
    if not enabled:
        return CheckResult(name="health_token", ok=True, message="未启用 HTTP 健康检查", level="INFO")

    host = os.getenv("HEALTHCHECK_HOST", "127.0.0.1")
    token = os.getenv("HEALTHCHECK_TOKEN", "").strip()
    if _is_loopback_host(host):
        return CheckResult(name="health_token", ok=True, message="健康检查仅本机暴露")
    if token:
        return CheckResult(name="health_token", ok=True, message="HEALTHCHECK_TOKEN 已配置")
    return CheckResult(
        name="health_token",
        ok=False,
        message="健康检查对外暴露时必须设置 HEALTHCHECK_TOKEN",
    )


def _check_database_url() -> CheckResult:
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")
    if not db_url.startswith("sqlite"):
        return CheckResult(name="database_url", ok=True, message="非 SQLite，已跳过文件路径检查")

    try:
        url = make_url(db_url)
    except Exception as exc:
        return CheckResult(name="database_url", ok=False, message=f"DATABASE_URL 无法解析: {exc}")

    db_path = url.database
    if not db_path or db_path == ":memory:":
        return CheckResult(name="database_url", ok=True, message="SQLite 内存数据库")

    path = Path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return CheckResult(name="database_url", ok=False, message=f"无法创建数据库目录: {exc}")

    if path.exists() and path.is_dir():
        return CheckResult(name="database_url", ok=False, message=f"数据库路径是目录而非文件: {path}")

    return CheckResult(name="database_url", ok=True, message=f"数据库路径可用: {path}")


def _check_instance_lock_path() -> CheckResult:
    lock_path_text = os.getenv("INSTANCE_LOCK_FILE", "").strip()
    if not lock_path_text:
        return CheckResult(
            name="instance_lock",
            ok=True,
            message="未显式设置 INSTANCE_LOCK_FILE（将使用默认推导路径）",
            level="WARN",
        )

    lock_path = Path(lock_path_text)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return CheckResult(name="instance_lock", ok=False, message=f"无法创建实例锁目录: {exc}")
    return CheckResult(name="instance_lock", ok=True, message=f"实例锁路径可用: {lock_path}")


def _check_greenlet() -> CheckResult:
    try:
        import greenlet  # noqa: F401
    except ModuleNotFoundError:
        return CheckResult(
            name="greenlet",
            ok=False,
            message="缺少运行依赖 greenlet（SQLAlchemy asyncio 必需）",
        )
    return CheckResult(name="greenlet", ok=True, message="greenlet 已安装")


async def _strict_db_checks() -> CheckResult:
    try:
        from src.db import init_db, verify_schema_version

        await init_db()
        await verify_schema_version()
        return CheckResult(name="strict_db", ok=True, message="数据库初始化与 schema 校验通过")
    except Exception as exc:
        return CheckResult(name="strict_db", ok=False, message=f"数据库严格校验失败: {exc}")
    finally:
        try:
            from src.db import dispose_engine

            await dispose_engine()
        except Exception:
            pass


def _print_results(results: Iterable[CheckResult]) -> int:
    failed = 0
    warns = 0
    for item in results:
        if item.ok:
            icon = "✅"
        else:
            icon = "❌"
            if item.level == "ERROR":
                failed += 1
        if item.level == "WARN" and item.ok:
            warns += 1
        print(f"{icon} [{item.name}] {item.message}")

    print("-" * 60)
    print(f"汇总：失败 {failed} 项，提示 {warns} 项")
    return failed


def _build_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    results.append(_check_required("BOT_TOKEN"))

    has_ai_key = bool(os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("API_KEY", "").strip())
    if has_ai_key:
        results.append(CheckResult(name="ai_key", ok=True, message="AI API Key 已配置"))
    elif _env_truthy("ALLOW_START_WITHOUT_AI"):
        results.append(
            CheckResult(
                name="ai_key",
                ok=True,
                message="未配置 AI Key，但 ALLOW_START_WITHOUT_AI=1",
                level="WARN",
            )
        )
    else:
        results.append(
            CheckResult(
                name="ai_key",
                ok=False,
                message="缺少 OPENAI_API_KEY/API_KEY（或设置 ALLOW_START_WITHOUT_AI=1）",
            )
        )

    if _is_production():
        results.append(_check_encryption_key())
        results.append(_check_access_control())
        results.append(_check_health_token())
    else:
        results.append(
            CheckResult(
                name="production_guard",
                ok=True,
                message="当前非生产环境，已跳过生产强制项",
                level="INFO",
            )
        )

    numeric_checks = [
        ("MAX_CONCURRENT_REPLIES", int),
        ("MAX_PENDING_REPLY_TASKS", int),
        ("AUTO_REPLY_COOLDOWN_SECONDS", int),
        ("SHUTDOWN_GRACE_PERIOD_SECONDS", int),
        ("AI_TIMEOUT_SECONDS", float),
        ("AI_MAX_RETRIES", int),
        ("DB_BUSY_TIMEOUT_MS", int),
        ("LOG_QUEUE_MAXSIZE", int),
        ("LOG_BATCH_SIZE", int),
        ("LOG_BATCH_INTERVAL", float),
        ("HEALTHCHECK_PORT", int),
    ]
    for name, caster in numeric_checks:
        results.append(_check_numeric(name, caster))

    results.append(_check_database_url())
    results.append(_check_instance_lock_path())
    results.append(_check_greenlet())

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="tg-auto-reply 生产就绪预检")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="额外执行数据库初始化与 schema 版本校验（会连接数据库）",
    )
    args = parser.parse_args()

    load_dotenv()
    print("🔍 开始执行生产就绪预检...")

    results = _build_checks()
    if args.strict:
        results.append(asyncio.run(_strict_db_checks()))

    failed = _print_results(results)
    if failed:
        print("❌ 预检未通过，请先修复失败项再上线。")
        return 1

    print("✅ 预检通过，可进入发布流程。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
