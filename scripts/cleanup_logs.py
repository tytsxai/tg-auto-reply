#!/usr/bin/env python3
import asyncio
import os
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _parse_retention_days() -> int:
    if len(sys.argv) > 1:
        value = sys.argv[1].strip()
        if not value.isdigit():
            raise ValueError("用法：cleanup_logs.py [保留天数]")
        return int(value)
    return int(os.getenv("LOG_RETENTION_DAYS", "90"))


async def main() -> None:
    load_dotenv()
    retention_days = _parse_retention_days()
    if retention_days <= 0:
        raise ValueError("保留天数必须大于 0")

    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")
    cutoff = datetime.utcnow() - timedelta(days=retention_days)

    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM message_logs WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        deleted = result.rowcount or 0
    await engine.dispose()
    print(f"✅ 已清理 {deleted} 条日志（保留 {retention_days} 天）")


if __name__ == "__main__":
    asyncio.run(main())
