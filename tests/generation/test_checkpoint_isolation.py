"""对话状态不在业务库（T-09；S5 提示词第十二节 test_checkpoint_isolation）。

覆盖（ADR-03 / FR-67/68 / AC-13）：用**真实 Postgres Checkpointer**跑一次含中断的生成，
断言对话 / 工作流状态落在 `etl_checkpoint` 库，业务库 `agent_runs` 无任何对话正文。
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.generation.checkpoint import open_checkpointer
from etl_agent.infrastructure.database import settings as infra_settings
from etl_agent.infrastructure.models import AgentRun
from tests.generation.test_telemetry import ALLOWED_AGENT_RUN_COLUMNS
from tests.helpers import SeededWorkspace, build_test_app, make_client, make_settings


@pytest.fixture
async def checkpoint_conn() -> AsyncIterator[asyncpg.Connection]:
    """检查点库直连（仅用于核对状态确实落在 etl_checkpoint）。"""
    conn = await asyncpg.connect(infra_settings.checkpoint_dsn)
    try:
        yield conn
    finally:
        await conn.close()


async def test_checkpoint_isolation(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_profiles: dict[str, uuid.UUID],
    checkpoint_conn: asyncpg.Connection,
) -> None:
    """对话 / 工作流状态在 etl_checkpoint；业务库无 Prompt 正文（FR-67/68）。"""
    requirement_marker = f"REQ-MARKER-{uuid.uuid4().hex}"
    settings = make_settings()
    thread_id = ""
    async with open_checkpointer(settings) as checkpointer:
        app: FastAPI = await build_test_app(db_session, checkpointer=checkpointer)
        async with make_client(app) as client:
            response = await client.post(
                f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
                json={"requirement": requirement_marker},
                headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "WAITING_INPUT"
        thread_id = body["thread_id"]

    try:
        # 1) 对话 / 工作流状态确实落在 etl_checkpoint（LangGraph 自动建表）
        checkpoint_rows = await checkpoint_conn.fetchval(
            "SELECT count(*) FROM checkpoints WHERE thread_id = $1", thread_id
        )
        assert checkpoint_rows > 0
        blobs = await checkpoint_conn.fetch(
            "SELECT channel, type FROM checkpoint_blobs WHERE thread_id = $1", thread_id
        )
        assert blobs, "Checkpoint 载荷未落 etl_checkpoint 库"

        # 2) 业务库 agent_runs 字段白名单：无任何承载 Prompt / 候选正文的列（FR-67）
        columns = (
            (
                await db_session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'agent_runs'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(columns) == ALLOWED_AGENT_RUN_COLUMNS

        # 3) 业务库该运行行不含需求正文（正文只在 Checkpoint 层）
        run = (
            await db_session.execute(
                select(AgentRun).where(AgentRun.thread_id == thread_id)
            )
        ).scalar_one()
        dumped = " ".join(str(getattr(run, name)) for name in columns)
        assert requirement_marker not in dumped
    finally:
        # 只清理本次用例自己写入的 thread（按 UUID 精确定位，不触碰其他数据）
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await checkpoint_conn.execute(f"DELETE FROM {table} WHERE thread_id = $1", thread_id)
