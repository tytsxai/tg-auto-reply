#!/usr/bin/env python3
import asyncio
import os

from dotenv import load_dotenv

from src.db import get_schema_version, migrate_db, SCHEMA_VERSION


async def main() -> None:
    load_dotenv()
    target = os.getenv("TARGET_SCHEMA_VERSION")
    target_version = int(target) if target and target.isdigit() else None

    current = await get_schema_version()
    new_version = await migrate_db(target_version)
    print(
        f"✅ 数据库迁移完成: {current} -> {new_version} "
        f"(目标 {target_version or SCHEMA_VERSION})"
    )


if __name__ == "__main__":
    asyncio.run(main())
