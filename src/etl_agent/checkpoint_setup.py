"""LangGraph Checkpoint 库初始化（T-01.6 / ADR-03 双库分离）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.3 章 / ADR-03
覆盖 FR：FR-67/68（Conversation / Workflow State 的物理载体）
对应 AC：AC-13（三层状态隔离）

在 etl_checkpoint 库创建 LangGraph Postgres Checkpointer 所需表；
幂等（CREATE TABLE IF NOT EXISTS），可重复执行。

注意：
1. langgraph-checkpoint-postgres 基于 psycopg（v3），不接受 asyncpg 协议串，
   因此这里使用 Settings.checkpoint_dsn（剥离子协议后的 psycopg DSN）；
2. Windows 上 psycopg 异步模式不兼容默认 ProactorEventLoop，
   需显式使用 SelectorEventLoop（psycopg 官方建议）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.3 章三层状态隔离物理分布 / ADR-03
覆盖 FR：FR-67（Conversation State）、FR-68（Workflow State）——二者均由本模块初始化的
Postgres Checkpointer 承载，物理位于独立的 etl_checkpoint 库
对应 AC：AC-13（三层状态隔离：对话/工作流状态不落业务库）
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any

from etl_agent.infrastructure.database import settings


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """运行协程：Windows 下使用 SelectorEventLoop（psycopg 异步兼容）。"""
    # 为什么绕开默认事件循环（S1 遗留问题 3）：psycopg 异步模式依赖 Selector 系列，
    # 而 Windows 上 asyncio 默认是 ProactorEventLoop——不显式指定则建表直接失败；
    # Linux 生产环境 asyncio.run 默认即 Selector，两条路径行为一致，不影响可移植性
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
