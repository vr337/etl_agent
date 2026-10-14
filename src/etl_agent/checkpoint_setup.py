"""LangGraph Checkpoint 库初始化（T-01.6 / ADR-03 双库分离）。

在 etl_checkpoint 库创建 LangGraph Postgres Checkpointer 所需表；
幂等（CREATE TABLE IF NOT EXISTS），可重复执行。

注意：
1. langgraph-checkpoint-postgres 基于 psycopg（v3），不接受 asyncpg 协议串，
   因此这里使用 Settings.checkpoint_dsn（剥离子协议后的 psycopg DSN）；
2. Windows 上 psycopg 异步模式不兼容默认 ProactorEventLoop，
   需显式使用 SelectorEventLoop（psycopg 官方建议）。
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any

from etl_agent.infrastructure.database import settings


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """运行协程：Windows 下使用 SelectorEventLoop（psycopg 异步兼容）。"""
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)


async def setup_checkpoint_schema() -> None:
    """创建 Checkpoint 表（幂等）。"""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_dsn) as saver:
        await saver.setup()


def main() -> None:
    _run_async(setup_checkpoint_schema())
    print("checkpoint schema ready (etl_checkpoint)")


if __name__ == "__main__":
    main()
