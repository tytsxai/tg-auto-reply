#!/usr/bin/env python3
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import get_schema_version, migrate_db, dispose_engine, SCHEMA_VERSION


async def main() -> None:
    try:
        # Optional: restrict migration target version.
        target = os.getenv("TARGET_SCHEMA_VERSION")
        target_version = int(target) if target and target.isdigit() else None

        current = await get_schema_version()
        new_version = await migrate_db(target_version)
        print(
            f"✅ 数据库迁移完成: {current} -> {new_version} "
            f"(目标 {target_version or SCHEMA_VERSION})"
        )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
