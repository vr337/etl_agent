"""M-L 受管回滚 — 回滚制品到期清理申请（FR-48）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 rollback_cleanup_requests /
第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-48（保留期到期 → 生成清理申请 → 走 Harness 协议执行）
对应 AC：AC-08（清理不绕过审批；制品按期清理不无限堆积）

**清理两步走**（禁止事项 17：不绕过 Harness 协议）：

```
1. 扫描到期制品（`retention.is_cleanable`）→ 落 `rollback_cleanup_requests`（PENDING）
2. 走 Harness 副作用协议（`CLEANUP_DORIS` → PDP → 双人审批 → Commit → Outbox）执行
```

本模块只做第 1 步（生成清理申请，纯治理事实）——**不**调 `side_effects`，
避免「扫描任务顺手把破坏性动作做了」。第 2 步由运维经 API-13 / Harness 协议发起。

**幂等**：同一回滚制品已有未完成的清理申请（PENDING）时不重复建单，
并把制品推进到 `CLEANUP_PENDING`（表达「已在清理队列中」）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.service import record_event
from etl_agent.infrastructure.models import (
    ExecutionRun,
    Pipeline,
    PipelineVersion,
    RollbackCleanupRequest,
    RollbackRequest,
)
from etl_agent.rollback.artifacts import (
    STATUS_CLEANUP_PENDING,
    STATUS_INVALID_METADATA,
    STATUS_ROLLED_BACK,
    transition_status,
)
from etl_agent.rollback.retention import (
    DEFAULT_RETENTION_DAYS,
    RetentionStatus,
    evaluate_retention,
    is_cleanable,
)

# 清理申请状态
STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_EXECUTED = "EXECUTED"
STATUS_FAILED = "FAILED"

ALL_CLEANUP_STATUSES: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_EXECUTED,
    STATUS_FAILED,
)

# 可进入清理队列的制品状态（未回滚完毕的制品永远不可清理）
_CLEANABLE_ARTIFACT_STATUSES: frozenset[str] = frozenset(
    {STATUS_ROLLED_BACK, STATUS_CLEANUP_PENDING, STATUS_INVALID_METADATA}
)


@dataclass(frozen=True)
class CleanupScanResult:
    """一次到期清理扫描的结论（供 Worker 任务返回与运行中心展示）。"""

    evaluated: int
    created: int
    skipped: int
    retention: RetentionStatus
    details: tuple[dict[str, str], ...] = ()


async def scan_expired_rollbacks(
    session: AsyncSession,
    *,
    requested_by: uuid.UUID,
    project_id: uuid.UUID | None = None,
    retention_days: int | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> CleanupScanResult:
    """扫描已过保留期且可清理的回滚制品，生成清理申请（幂等）。

    Args:
        requested_by: 清理申请的系统主体（Worker 身份，非人类用户）。
        project_id: 项目边界（给定则只扫本项目制品；API 路径必传，Worker 周期任务可留空）。
        retention_days: 保留期覆盖（缺省按每张制品自身清单值）。
        limit: 单遍扫描上限（防止一次扫全表）。
        now: 时间锚点（测试可注入）。

    Returns:
        CleanupScanResult: 评估 / 建单 / 跳过计数与保留期结论。
    """
    moment = now or datetime.now(UTC)
    statement = (
        select(RollbackRequest)
        .where(RollbackRequest.artifact_status.in_(list(_CLEANABLE_ARTIFACT_STATUSES)))
        .order_by(RollbackRequest.created_at)
        .limit(limit)
    )
    if project_id is not None:
        statement = (
            statement.join(ExecutionRun, ExecutionRun.id == RollbackRequest.run_id)
            .join(PipelineVersion, PipelineVersion.id == ExecutionRun.pipeline_version_id)
            .join(Pipeline, Pipeline.id == PipelineVersion.pipeline_id)
            .where(Pipeline.project_id == project_id)
        )
    rows = (await session.execute(statement)).scalars().all()
    details: list[dict[str, str]] = []
    created = 0
    skipped = 0
    for rollback in rows:
        if not is_cleanable(rollback, now=moment):
            skipped += 1
            continue
        existing = await _pending_cleanup(session, rollback.id)
        if existing is not None:
            skipped += 1
            continue
        request = RollbackCleanupRequest(
            rollback_request_id=rollback.id,
            status=STATUS_PENDING,
            reason=f"回滚制品超过保留期（{retention_days or _retention_of(rollback)} 天）",
            requested_by=requested_by,
        )
        session.add(request)
        _advance_artifact(rollback, STATUS_CLEANUP_PENDING)
        await session.flush()
        await record_event(
            session,
            rollback.run_id,
            level="INFO",
            event_type="rollback.cleanup_requested",
            message=f"到期清理申请已创建（{request.id}）：回滚制品 {rollback.id}",
            stage="rollback",
        )
        details.append({"rollback_request_id": str(rollback.id), "cleanup_id": str(request.id)})
        created += 1
    return CleanupScanResult(
        evaluated=len(rows),
        created=created,
        skipped=skipped,
        retention=evaluate_retention(list(rows), now=moment, retention_days=retention_days),
        details=tuple(details),
    )


async def _pending_cleanup(
    session: AsyncSession, rollback_id: uuid.UUID
) -> RollbackCleanupRequest | None:
    """既有未完成的清理申请（幂等键：同制品的 PENDING）。"""
    return (
        (
            await session.execute(
                select(RollbackCleanupRequest).where(
                    RollbackCleanupRequest.rollback_request_id == rollback_id,
                    RollbackCleanupRequest.status == STATUS_PENDING,
                )
            )
        )
        .scalars()
        .first()
    )


def _advance_artifact(rollback: RollbackRequest, target: str) -> None:
    """推进制品状态（非法迁移静默保留，不阻塞扫描）。"""
    current = rollback.artifact_status or STATUS_ROLLED_BACK
    try:
        rollback.artifact_status = transition_status(current, target)
    except Exception:  # noqa: BLE001 -- 单个制品状态异常不应中断整遍扫描
        rollback.artifact_status = current


def _retention_of(rollback: RollbackRequest) -> int:
    """制品自身保留期（缺省 7 天）。"""
    return int(getattr(rollback, "retention_days", None) or DEFAULT_RETENTION_DAYS)


__all__ = [
    "ALL_CLEANUP_STATUSES",
    "STATUS_APPROVED",
    "STATUS_EXECUTED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "CleanupScanResult",
    "scan_expired_rollbacks",
]
