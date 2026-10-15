"""Worker 心跳用例（T-13.3 / FR-38）。

覆盖：心跳 upsert 幂等、能力声明不含环境秘密、失活阈值配置可覆盖。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.side_effects.registry import ALLOWED_COMMANDS, MAIN_EXECUTION_COMMAND
from etl_agent.infrastructure.models import WorkerHeartbeat
from etl_agent.observability.health import worker_summary
from etl_agent.worker.tasks.heartbeat import (
    report_heartbeat,
    worker_capabilities,
    worker_identity,
)
from tests.helpers import make_settings


def test_worker_identity_is_host_and_pid() -> None:
    """Worker 身份由主机名 + PID 组成（容器重建即换身份，旧行由失活判定淘汰）。"""
    identity = worker_identity()
    assert identity
    assert "-" in identity


def test_capabilities_declare_commands_without_secrets() -> None:
    """能力声明只含命令清单与引擎类型（不含任何环境秘密，AC-02）。"""
    capabilities = worker_capabilities()
    assert capabilities["engine"] == "seatunnel"
    assert capabilities["main_command"] == MAIN_EXECUTION_COMMAND
    assert set(capabilities["commands"]) == set(ALLOWED_COMMANDS)


async def test_heartbeat_upserts_single_row(db_session: AsyncSession) -> None:
    """同一 worker_id 重复上报只保留一行并刷新时间（幂等，FR-38）。"""
    first = await report_heartbeat(db_session, worker_id="worker-x")
    row = await db_session.get(WorkerHeartbeat, "worker-x")
    assert row is not None
    first_seen = row.last_heartbeat_at

    second = await report_heartbeat(db_session, worker_id="worker-x")

    assert first == second == "worker-x"
    total = (
        await db_session.execute(
            select(func.count()).select_from(WorkerHeartbeat).where(
                WorkerHeartbeat.worker_id == "worker-x"
            )
        )
    ).scalar()
    assert total == 1
    refreshed = await db_session.get(WorkerHeartbeat, "worker-x")
    assert refreshed is not None
    assert refreshed.last_heartbeat_at >= first_seen


async def test_worker_summary_counts_active_only(db_session: AsyncSession) -> None:
    """健康摘要按失活阈值区分活跃 / 全量（35s 默认阈值，FR-38）。

    断言用**基线差值**：开发库可能残留真实 Worker 的心跳行（容器化验收会写库），
    直接断言绝对值会让用例依赖外部状态。
    """
    settings = make_settings(worker_inactive_seconds=35)
    now = datetime.now(UTC)
    baseline = await worker_summary(db_session, settings, now=now)

    db_session.add_all(
        [
            WorkerHeartbeat(
                worker_id=f"alive-{uuid.uuid4().hex[:8]}",
                last_heartbeat_at=now - timedelta(seconds=5),
                capabilities={"engine": "seatunnel"},
            ),
            WorkerHeartbeat(
                worker_id=f"dead-{uuid.uuid4().hex[:8]}",
                last_heartbeat_at=now - timedelta(seconds=120),
                capabilities={"engine": "seatunnel"},
            ),
        ]
    )
    await db_session.flush()

    summary = await worker_summary(db_session, settings, now=now)

    assert summary["total"] == baseline["total"] + 2
    assert summary["active"] == baseline["active"] + 1
    assert summary["inactive_after_seconds"] == 35
    assert summary["latest_heartbeat_at"] is not None
