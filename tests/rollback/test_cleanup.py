"""回滚制品到期清理扫描用例（T-16.18，FR-48，AC-08）。

覆盖：到期且已回滚 → 生成清理申请；未到期 / 未回滚完毕 → 跳过；
幂等（同制品已有 PENDING 清理申请不重复建单）；项目边界（只扫本项目）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import ExecutionRun, RollbackCleanupRequest, RollbackRequest
from etl_agent.rollback.artifacts import (
    STATUS_CLEANUP_PENDING,
    STATUS_ROLLBACK_PENDING,
    STATUS_ROLLED_BACK,
    build_manifest,
)
from etl_agent.rollback.cleanup import STATUS_PENDING, scan_expired_rollbacks
from etl_agent.rollback.service import CreateRollbackInput, create_rollback_request
from tests.helpers import SeededWorkspace

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


async def _expired_rollback(
    session: AsyncSession,
    seeded: SeededWorkspace,
    make_run,
    *,
    days_old: int = 30,
    artifact_status: str = STATUS_ROLLED_BACK,
):
    """造一条指定年龄、指定制品状态的回滚申请。"""
    run = await make_run(status="FAILED")
    manifest = build_manifest(
        run_id=run.id,
        target_table="orders_wide",
        shadow_table="orders_wide_shadow",
        error_table=None,
        retention_days=7,
        capacity_warn_threshold=20,
        now=NOW - timedelta(days=days_old),
    )
    request = await create_rollback_request(
        session,
        payload=CreateRollbackInput(
            run_id=run.id, requested_by=seeded.engineer_a_user_id, reason="测试"
        ),
        manifest=manifest,
        now=NOW - timedelta(days=days_old),
    )
    request.artifact_status = artifact_status
    await session.flush()
    return request


async def test_cleanup_scan_creates_for_expired_rolled_back(
    db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """到期 + 已回滚 → 生成清理申请，制品推进到 CLEANUP_PENDING。"""
    request = await _expired_rollback(db_session, seeded, make_run)
    result = await scan_expired_rollbacks(db_session, requested_by=seeded.admin_a_user_id, now=NOW)
    assert result.evaluated == 1
    assert result.created == 1
    assert result.skipped == 0
    assert request.artifact_status == STATUS_CLEANUP_PENDING

    cleanup = (await db_session.execute(select(RollbackCleanupRequest))).scalars().all()
    assert len(cleanup) == 1
    assert cleanup[0].status == STATUS_PENDING
    assert cleanup[0].rollback_request_id == request.id


async def test_cleanup_scheduled(
    db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """到期清理扫描可触发（提示词 §9 tests/rollback 清单别名）。

    到期 + 已回滚的制品经一次扫描即建单（具体断言由
    `test_cleanup_scan_creates_for_expired_rolled_back` 覆盖）。
    """
    request = await _expired_rollback(db_session, seeded, make_run)
    result = await scan_expired_rollbacks(db_session, requested_by=seeded.admin_a_user_id, now=NOW)
    assert result.created == 1
    assert request.artifact_status == STATUS_CLEANUP_PENDING


async def test_cleanup_scan_skips_fresh(
    db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """未到期 → 跳过，不建单。"""
    await _expired_rollback(db_session, seeded, make_run, days_old=1)
    result = await scan_expired_rollbacks(db_session, requested_by=seeded.admin_a_user_id, now=NOW)
    assert result.created == 0
    assert result.skipped == 1


async def test_cleanup_scan_skips_not_rolled_back(
    db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """到期但未回滚完毕 → 不进入扫描范围（不可清理）。"""
    request = await _expired_rollback(
        db_session, seeded, make_run, artifact_status=STATUS_ROLLBACK_PENDING
    )
    result = await scan_expired_rollbacks(db_session, requested_by=seeded.admin_a_user_id, now=NOW)
    assert result.evaluated == 0  # 状态过滤直接排除
    assert request.artifact_status == STATUS_ROLLBACK_PENDING


async def test_cleanup_scan_idempotent(
    db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """同制品已有 PENDING 清理申请 → 二次扫描不重复建单（幂等）。"""
    await _expired_rollback(db_session, seeded, make_run)
    first = await scan_expired_rollbacks(db_session, requested_by=seeded.admin_a_user_id, now=NOW)
    second = await scan_expired_rollbacks(db_session, requested_by=seeded.admin_a_user_id, now=NOW)
    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1
    cleanup = (await db_session.execute(select(RollbackCleanupRequest))).scalars().all()
    assert len(cleanup) == 1


async def test_cleanup_scan_project_boundary(
    db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """指定 project_id 时只扫本项目制品（多租户边界）。"""
    await _expired_rollback(db_session, seeded, make_run)
    result = await scan_expired_rollbacks(
        db_session,
        requested_by=seeded.admin_a_user_id,
        project_id=seeded.project_b_id,
        now=NOW,
    )
    assert result.created == 0
    assert result.evaluated == 0

    result_a = await scan_expired_rollbacks(
        db_session,
        requested_by=seeded.admin_a_user_id,
        project_id=seeded.project_a_id,
        now=NOW,
    )
    assert result_a.created == 1


async def test_cleanup_scan_reports_retention_summary(
    db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """扫描结果带保留期结论与预警标志。"""
    await _expired_rollback(db_session, seeded, make_run)
    result = await scan_expired_rollbacks(db_session, requested_by=seeded.admin_a_user_id, now=NOW)
    assert result.retention.total == 1
    assert result.retention.expired == 1
    assert result.retention.capacity_warning is False
    assert result.details and result.details[0]["cleanup_id"]


async def test_cleanup_scan_records_event(
    db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """清理申请落库同时写事件流（治理事实可追溯）。"""
    from etl_agent.infrastructure.models import ExecutionEvent

    await _expired_rollback(db_session, seeded, make_run)
    await scan_expired_rollbacks(db_session, requested_by=seeded.admin_a_user_id, now=NOW)
    events = (await db_session.execute(select(ExecutionEvent))).scalars().all()
    assert any(e.event_type == "rollback.cleanup_requested" for e in events)


async def test_cleanup_scan_no_side_effect_invocation(
    db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """扫描只建清理申请这一治理事实，不触发任何数据面动作（禁止事项 17）。"""
    from etl_agent.infrastructure.models import HarnessCommandOutbox

    await _expired_rollback(db_session, seeded, make_run)
    await scan_expired_rollbacks(db_session, requested_by=seeded.admin_a_user_id, now=NOW)
    outbox = (await db_session.execute(select(HarnessCommandOutbox))).scalars().all()
    assert outbox == []  # 未走 Harness 协议，无命令下发


def _unused(seeded: SeededWorkspace) -> uuid.UUID:  # pragma: no cover
    return seeded.project_a_id


_ = ExecutionRun
_ = RollbackRequest
