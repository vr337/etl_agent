"""M-F 生成引擎 — LangGraph Postgres Checkpointer 集成（双库分离）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.3 章三层状态隔离物理分布 / ADR-03
覆盖 FR：FR-67（对话状态不落业务库）、FR-68（工作流状态跨请求恢复）
对应 AC：AC-03（同 thread_id 恢复）、AC-13（三层状态隔离）

红线（ADR-03 / S5 禁止事项 14）：
对话历史与 LangGraph Checkpoint **必须**落在 `etl_checkpoint` 库，禁止落业务库 `etl_agent`。
本模块只暴露连接 `etl_checkpoint` 的 saver，物理上无法写到业务库。

Windows 注意事项：langgraph-checkpoint-postgres 基于 psycopg（v3），
异步模式不接受 ProactorEventLoop——uvicorn 在 Windows 默认已切 Selector，
测试侧需在 conftest 显式设置（与 `checkpoint_setup` 的既有处置一致）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from etl_agent.config import Settings


def checkpoint_dsn(settings: Settings) -> str:
    """检查点库 psycopg DSN（剥离子协议；S1 `checkpoint_dsn` 同源口径）。"""
    from etl_agent.infrastructure.database import settings as infra_settings

    if settings.checkpoint_database_url:
        return settings.checkpoint_database_url.replace("+asyncpg", "")
    return infra_settings.checkpoint_dsn


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[Any]:
    """打开连到 `etl_checkpoint` 库的 Checkpointer（应用生命周期内复用）。

    调用方负责 `async with` 生命周期；应用启动时建一次、关闭时释放，
    避免每请求新建连接（SPEC 8「高可用 · 控制面无单点」）。
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn(settings)) as saver:
        yield saver
