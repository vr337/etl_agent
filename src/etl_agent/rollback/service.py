"""M-L 受管回滚 — 回滚申请 / 审批 / 执行状态机（FR-48，AC-08 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 rollback_requests /
第 6.1 章 API-13 / 第 6.4 章错误码 / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-48（回滚申请 → 审批 → 执行；制品 5 态；保留期 7 天）
对应 AC：AC-08（回滚走 Harness 协议，审批后方可执行；执行落原状态）

**两条状态机**（都单向）：

回滚申请状态（`rollback_requests.status`）：

```
PENDING ──(审批 APPROVE)──► APPROVED ──(创建副作用准备单)──► EXECUTING ──(执行完成)──► EXECUTED
   │                            │                                │
   └──(审批 REJECT)──► REJECTED └──(取消)──► CANCELLED            └──(失败)──► FAILED
```

制品状态（`rollback_requests.artifact_status`，见 `artifacts.py`）：

```
AVAILABLE ──► ROLLBACK_PENDING ──► ROLLED_BACK ──► CLEANUP_PENDING
                                        ▲
任意 ──► INVALID_METADATA（终态）
```

**申请 ≠ 执行**：`create_rollback_request` 只落「回滚意图 + 制品清单」，
真正的数据面动作必须走 `side_effects.prepare_side_effect`（PDP 默认 P0 → 双人审批）
→ 四眼审批 → Commit → Outbox → Worker。本模块的 `mark_executing` 由
Worker 消费副作用令牌后回调，**不是** API 路径能直接推进的状态。

**制品状态与申请状态解耦**：申请是治理动作，制品是数据面事实。
申请 REJECTED / CANCELLED 时制品从 ROLLBACK_PENDING 退回 AVAILABLE
（还能重新申请）；申请 EXECUTED 时制品才推进到 ROLLED_BACK。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.audit.hooks import on_rollback_event
from etl_agent.execution.service import record_event
from etl_agent.infrastructure.models import ExecutionRun, RollbackRequest
from etl_agent.rollback.artifacts import (
    STATUS_AVAILABLE,
    STATUS_INVALID_METADATA,
    STATUS_ROLLBACK_PENDING,
    STATUS_ROLLED_BACK,
    build_manifest,
    manifest_shadow_table,
    transition_status,
    validate_manifest,
)
from etl_agent.rollback.errors import RollbackNotFound, RollbackStateConflict
from etl_agent.rollback.retention import (
    DEFAULT_CAPACITY_WARN_THRESHOLD,
    resolve_capacity_threshold,
    resolve_retention_days,
)

# 回滚申请状态（SPEC 5.2 rollback_requests.status）
STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_EXECUTING = "EXECUTING"
STATUS_EXECUTED = "EXECUTED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"

ALL_REQUEST_STATUSES: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_EXECUTING,
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_CANCELLED,
)

# 申请状态迁移白名单（单向；FAILED 允许退回 EXECUTING 供重试）
REQUEST_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PENDING: frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_CANCELLED}),
    STATUS_APPROVED: frozenset({STATUS_EXECUTING, STATUS_CANCELLED}),
    STATUS_EXECUTING: frozenset({STATUS_EXECUTED, STATUS_FAILED}),
    STATUS_FAILED: frozenset({STATUS_EXECUTING}),
    STATUS_REJECTED: frozenset(),
    STATUS_EXECUTED: frozenset(),
    STATUS_CANCELLED: frozenset(),
}

TERMINAL_REQUEST_STATUSES: frozenset[str] = frozenset(
    {STATUS_REJECTED, STATUS_EXECUTED, STATUS_CANCELLED}
)

DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"


@dataclass(frozen=True)
class CreateRollbackInput:
    """回滚申请入参（只含治理事实与引用，不含凭据与数据）。"""

    run_id: uuid.UUID
    requested_by: uuid.UUID
    reason: str
    target_table: str | None = None
    shadow_table: str | None = None
    error_table: str | None = None
    retention_days: int | None = None
    capacity_warn_threshold: int | None = None


def can_transition(current: str, target: str) -> bool:
    """回滚申请状态迁移白名单判定。"""
    return target in REQUEST_TRANSITIONS.get(current, frozenset())


def is_terminal(status: str) -> bool:
    return status in TERMINAL_REQUEST_STATUSES


async def load_rollback(session: AsyncSession, rollback_id: uuid.UUID) -> RollbackRequest:
    """按 ID 取回滚申请（不存在即 404，不泄露存在性）。

    Raises:
        RollbackNotFound: 申请不存在。
    """
    row = await session.get(RollbackRequest, rollback_id)
    if row is None:
        raise RollbackNotFound("回滚申请不存在")
    return row


async def load_rollback_project(session: AsyncSession, rollback_id: uuid.UUID) -> uuid.UUID:
    """反查回滚申请所属项目（API-13 路径无 project_id，需回溯项目做鉴权）。

    Raises:
        RollbackNotFound: 申请不存在，或其运行已不可追溯项目。
    """
    row = (
        await session.execute(
            select(ExecutionRun.pipeline_version_id)
            .join(RollbackRequest, RollbackRequest.run_id == ExecutionRun.id)
            .where(RollbackRequest.id == rollback_id)
        )
    ).one_or_none()
    if row is None:
        raise RollbackNotFound("回滚申请不存在")
    return await _project_of_version(session, row[0])


async def run_project(session: AsyncSession, run_id: uuid.UUID) -> uuid.UUID:
    """反查运行所属项目（申请入口的鉴权锚点）。

    Raises:
        RollbackNotFound: 运行不存在。
    """
    row = (
        await session.execute(
            select(ExecutionRun.pipeline_version_id).where(ExecutionRun.id == run_id)
        )
    ).one_or_none()
    if row is None:
        raise RollbackNotFound("执行运行不存在，无法申请回滚")
    return await _project_of_version(session, row[0])


async def _project_of_version(session: AsyncSession, version_id: uuid.UUID) -> uuid.UUID:
    """由流水线版本回溯项目（申请归项目所有，跨项目一律 404）。

    项目归属在 `pipelines.project_id`（`pipeline_versions` 只挂 `pipeline_id`），
    故按 version → pipeline → project 逐级回溯。
    """
    from etl_agent.infrastructure.models import Pipeline, PipelineVersion

    row = (
        await session.execute(
            select(Pipeline.project_id)
            .join(PipelineVersion, PipelineVersion.pipeline_id == Pipeline.id)
            .where(PipelineVersion.id == version_id)
        )
    ).one_or_none()
    if row is None:
        raise RollbackNotFound("流水线版本不存在，无法定位所属项目")
    return row[0]


async def create_rollback_request(
    session: AsyncSession,
    *,
    payload: CreateRollbackInput,
    manifest: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> RollbackRequest:
    """创建回滚申请（PENDING，落制品清单；不触发任何数据面动作）。

    Args:
        payload: 申请入参（run / 申请人 / 原因 / 保留期参数）。
        manifest: 显式制品清单；缺省由 payload 的表名 + 保留期事实构造。

    Raises:
        RollbackStateConflict: 同一运行已有未终结的回滚申请（防重复申请）。
    """
    existing = (
        (
            await session.execute(
                select(RollbackRequest).where(
                    RollbackRequest.run_id == payload.run_id,
                    RollbackRequest.status.in_([STATUS_PENDING, STATUS_APPROVED, STATUS_EXECUTING]),
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise RollbackStateConflict(
            f"该运行已有进行中的回滚申请（{existing.id}，状态 {existing.status}）"
        )
    resolved = manifest or _default_manifest(payload, now=now)
    validation = validate_manifest(resolved)
    request = RollbackRequest(
        run_id=payload.run_id,
        status=STATUS_PENDING,
        reason=payload.reason,
        requested_by=payload.requested_by,
        retention_days=resolve_retention_days(payload.retention_days),
        capacity_warn_threshold=resolve_capacity_threshold(payload.capacity_warn_threshold),
        artifact_manifest=resolved,
        artifact_status=STATUS_INVALID_METADATA
        if not validation.valid
        else STATUS_ROLLBACK_PENDING,
    )
    session.add(request)
    await session.flush()
    await record_event(
        session,
        payload.run_id,
        level="WARN",
        event_type="rollback.requested",
        message=f"回滚申请已创建（{request.id}）：{_brief(payload.reason)}",
        stage="rollback",
    )
    # 钩子 7（F3，REQUESTED 相位）：回滚申请双写证据账本。project_id 经申请入口
    # 既有的 run_project 反查（运行不存在则 RollbackNotFound——申请前置已鉴权，
    # 走到此处运行必然存在）。随调用方（路由 / Worker 会话）既有事务提交，
    # 钩子只 flush 不自行 commit。
    await on_rollback_event(
        session,
        project_id=await run_project(session, payload.run_id),
        rollback_id=request.id,
        phase="REQUESTED",
        actor_id=payload.requested_by,
        status=request.status,
        reason=payload.reason,
    )
    return request


async def decide_rollback(
    session: AsyncSession,
    *,
    rollback_id: uuid.UUID,
    decision: str,
    decided_by: uuid.UUID,
    reason: str | None = None,
    now: datetime | None = None,
) -> RollbackRequest:
    """审批回滚申请（APPROVE → APPROVED；REJECT → REJECTED 且制品退回）。

    与副作用四眼审批的分工：本审批是**回滚意图**的裁决（治理层，单审批人），
    真正执行前的四眼审批由 Harness 副作用准备单承载（`side_effects.decide_side_effect`）。

    Raises:
        RollbackStateConflict: 申请不在 PENDING，或决策值非法。
    """
    request = await load_rollback(session, rollback_id)
    if request.status != STATUS_PENDING:
        raise RollbackStateConflict(f"回滚申请状态 {request.status}，不可审批（FR-48）")
    if decision not in {DECISION_APPROVE, DECISION_REJECT}:
        raise RollbackStateConflict(f"未知审批决策 {decision}（FR-48）")
    if decision == DECISION_APPROVE:
        _advance(request, STATUS_APPROVED)
    else:
        _advance(request, STATUS_REJECTED)
        _rollback_artifact(request, STATUS_AVAILABLE)
    request.decided_by = decided_by
    request.decision = decision
    await session.flush()
    await record_event(
        session,
        request.run_id,
        level="INFO" if decision == DECISION_APPROVE else "WARN",
        event_type="rollback.decided",
        message=f"回滚申请 {decision}（{request.id}）：{_brief(reason or '')}",
        stage="rollback",
    )
    # 钩子 7（F3，DECIDED 相位）：审批决策双写证据账本（APPROVE / REJECT 均留痕）
    await on_rollback_event(
        session,
        project_id=await run_project(session, request.run_id),
        rollback_id=request.id,
        phase="DECIDED",
        actor_id=decided_by,
        status=request.status,
        reason=reason,
    )
    _ = now
    return request


async def mark_executing(session: AsyncSession, rollback_id: uuid.UUID) -> RollbackRequest:
    """推进到 EXECUTING（由 Worker 消费副作用令牌后回调，非 API 直达）。

    Raises:
        RollbackStateConflict: 申请未 APPROVED / 非重试中的 FAILED。
    """
    request = await load_rollback(session, rollback_id)
    _advance(request, STATUS_EXECUTING)
    request.executed_at = datetime.now(UTC)
    await session.flush()
    await record_event(
        session,
        request.run_id,
        level="INFO",
        event_type="rollback.executing",
        message=f"回滚执行中（{request.id}，走 Harness 副作用协议）",
        stage="rollback",
    )
    return request


async def mark_executed(
    session: AsyncSession, rollback_id: uuid.UUID, *, now: datetime | None = None
) -> RollbackRequest:
    """推进到 EXECUTED，并把制品推进到 ROLLED_BACK。

    Raises:
        RollbackStateConflict: 申请不在 EXECUTING。
    """
    request = await load_rollback(session, rollback_id)
    _advance(request, STATUS_EXECUTED)
    request.executed_at = now or datetime.now(UTC)
    _rollback_artifact(request, STATUS_ROLLED_BACK)
    await session.flush()
    await record_event(
        session,
        request.run_id,
        level="INFO",
        event_type="rollback.executed",
        message=f"回滚已执行完成（{request.id}）：影子表数据已清理，正式表未动（FR-48）",
        stage="rollback",
    )
    # 钩子 7（F3，EXECUTED 相位）：执行完成双写证据账本。本相位在 src 中暂无
    # Worker 驱动（S8 遗留），接线落在相位函数内——未来执行任务接入即自动留痕。
    await on_rollback_event(
        session,
        project_id=await run_project(session, request.run_id),
        rollback_id=request.id,
        phase="EXECUTED",
        status=request.status,
    )
    return request


async def mark_failed(
    session: AsyncSession, rollback_id: uuid.UUID, *, reason: str
) -> RollbackRequest:
    """推进到 FAILED（制品退回 AVAILABLE，保留再次回滚能力）。

    Raises:
        RollbackStateConflict: 申请不在 EXECUTING。
    """
    request = await load_rollback(session, rollback_id)
    _advance(request, STATUS_FAILED)
    _rollback_artifact(request, STATUS_AVAILABLE)
    await session.flush()
    await record_event(
        session,
        request.run_id,
        level="ERROR",
        event_type="rollback.failed",
        message=f"回滚执行失败（{request.id}）：{_brief(reason)}",
        stage="rollback",
    )
    return request


def _advance(request: RollbackRequest, target: str) -> None:
    """按白名单推进申请状态（非法迁移即拒绝）。"""
    if not can_transition(request.status, target):
        raise RollbackStateConflict(
            f"回滚申请状态 {request.status} 不可迁移到 {target}（FR-48 状态机）"
        )
    request.status = target


def _rollback_artifact(request: RollbackRequest, target: str) -> None:
    """推进制品状态（非法迁移静默保留——申请状态机是主判据）。"""
    current = request.artifact_status or STATUS_AVAILABLE
    try:
        request.artifact_status = transition_status(current, target)
    except RollbackStateConflict:
        request.artifact_status = current


def _default_manifest(payload: CreateRollbackInput, *, now: datetime | None) -> dict[str, Any]:
    """由入参构造制品清单（缺影子表名时以 `<target>_shadow` 兜底）。"""
    target = payload.target_table or ""
    shadow = payload.shadow_table or (f"{target}_shadow" if target else "")
    manifest = build_manifest(
        run_id=payload.run_id,
        target_table=target,
        shadow_table=shadow,
        error_table=payload.error_table,
        retention_days=resolve_retention_days(payload.retention_days),
        capacity_warn_threshold=resolve_capacity_threshold(
            payload.capacity_warn_threshold or DEFAULT_CAPACITY_WARN_THRESHOLD
        ),
        now=now,
    )
    return manifest


def manifest_of(request: RollbackRequest) -> dict[str, Any]:
    """取申请制品清单（非 dict 时回落空字典，避免下游 None 判空散落各处）。"""
    return request.artifact_manifest if isinstance(request.artifact_manifest, dict) else {}


async def cleanup_target_of(request: RollbackRequest) -> str | None:
    """回滚清理目标影子表名（数据面动作的目标；缺失即不可执行）。"""
    return manifest_shadow_table(request.artifact_manifest)


def _brief(text: str, limit: int = 200) -> str:
    """摘要截断（事件流只落治理事实，不含栈正文，禁止事项 16）。"""
    return " ".join(text.split())[:limit]


__all__ = [
    "ALL_REQUEST_STATUSES",
    "DECISION_APPROVE",
    "DECISION_REJECT",
    "REQUEST_TRANSITIONS",
    "STATUS_APPROVED",
    "STATUS_CANCELLED",
    "STATUS_EXECUTED",
    "STATUS_EXECUTING",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "CreateRollbackInput",
    "can_transition",
    "cleanup_target_of",
    "create_rollback_request",
    "decide_rollback",
    "is_terminal",
    "load_rollback",
    "load_rollback_project",
    "manifest_of",
    "mark_executed",
    "mark_executing",
    "mark_failed",
    "run_project",
]
