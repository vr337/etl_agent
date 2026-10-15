"""M-N 审计证据 — 关键事件钩子（自动记录 7 类治理动作）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 audit_events /
第 8 章「安全 · 审计」/ 第 5.2 章 harness_evidence_streams（证据账本联动）
覆盖 FR：FR-36（审计事件 + 证据账本双写）
对应 AC：AC-02（不含凭证明文）、AC-09（证据覆盖率门禁 → 关键事件必须有证据）

**7 类关键事件**（S9 提示词 §8.2）：
1. PDP 决策（ALLOW / DENY / REQUIRE_APPROVAL / DEFER）
2. Prepare 创建（准备单）
3. 审批决策（APPROVE / REJECT）
4. Commit 签发 Capability
5. 执行状态迁移
6. 监督动作（CONTINUE / THROTTLE / PAUSE / CANCEL / QUARANTINE）
7. 回滚申请 / 审批 / 执行

**双写语义**：每类钩子同时落 `audit_events`（人可读的治理台账）与
`harness_evidence_*`（防篡改证据账本）。二者职责不同——审计台账回答「谁做了什么」，
证据账本回答「这些记录有没有被动过」。证据写入失败不回滚审计
（证据覆盖率由 Benchmark 门禁度量，缺失会被 AC-09 抓出来，不能靠静默掩盖）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.audit.service import AuditEventInput, now_utc, record_safely
from etl_agent.evidence.append import EvidenceEventInput, append_event
from etl_agent.evidence.checkpoint import CheckpointSigner

logger = logging.getLogger("etl_agent.audit.hooks")

# 关键事件类型（落证据账本的 event_type；Benchmark 证据覆盖率据此核对）
EVENT_PDP_DECISION = "PDP_DECISION"
EVENT_PREPARE_CREATED = "PREPARE_CREATED"
EVENT_APPROVAL_DECISION = "APPROVAL_DECISION"
EVENT_COMMIT_ISSUED = "COMMIT_ISSUED"
EVENT_EXECUTION_TRANSITION = "EXECUTION_TRANSITION"
EVENT_SUPERVISION_ACTION = "SUPERVISION_ACTION"
EVENT_ROLLBACK_REQUESTED = "ROLLBACK_REQUESTED"
EVENT_ROLLBACK_DECIDED = "ROLLBACK_DECIDED"
EVENT_ROLLBACK_EXECUTED = "ROLLBACK_EXECUTED"

KEY_EVENT_TYPES: tuple[str, ...] = (
    EVENT_PDP_DECISION,
    EVENT_PREPARE_CREATED,
    EVENT_APPROVAL_DECISION,
    EVENT_COMMIT_ISSUED,
    EVENT_EXECUTION_TRANSITION,
    EVENT_SUPERVISION_ACTION,
    EVENT_ROLLBACK_REQUESTED,
    EVENT_ROLLBACK_DECIDED,
    EVENT_ROLLBACK_EXECUTED,
)


async def _dual_write(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str | None,
    trace_id: str | None,
    event_type: str,
    risk_level: str | None = None,
    policy_decision_id: uuid.UUID | None = None,
    signer: CheckpointSigner | None = None,
) -> None:
    """审计台账 + 证据账本双写（证据失败不影响审计，见模块 docstring）。"""
    actor = actor_id or uuid.UUID(int=0)  # 系统主体：无 actor 的动作记零 UUID
    await record_safely(
        session,
        AuditEventInput(
            project_id=project_id,
            actor_id=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=before,
            after=after,
            reason=reason,
            trace_id=trace_id,
        ),
    )
    try:
        await append_event(
            session,
            project_id=project_id,
            event=EvidenceEventInput(
                event_type=event_type,
                payload={
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "after": after or {},
                },
                actor_id=actor_id,
                risk_level=risk_level,
                policy_decision_id=policy_decision_id,
                trace_id=trace_id,
                occurred_at=now_utc(),
            ),
            signer=signer,
        )
    except Exception:
        logger.exception("证据账本追加失败：event_type=%s resource=%s", event_type, resource_id)


async def on_pdp_decision(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    decision_id: uuid.UUID,
    outcome: str,
    risk_level: str,
    actor_id: uuid.UUID | None = None,
    trace_id: str | None = None,
    signer: CheckpointSigner | None = None,
) -> None:
    """钩子 1：PDP 决策（ALLOW / DENY / REQUIRE_APPROVAL / DEFER）。

    DENY 是终局裁决，必须留痕——否则「为什么被拒」在事后无从复现（FR-29）。
    """
    await _dual_write(
        session,
        project_id=project_id,
        actor_id=actor_id,
        action="PDP_DECISION",
        resource_type="POLICY_DECISION",
        resource_id=str(decision_id),
        before=None,
        after={"outcome": outcome, "risk_level": risk_level},
        reason=None,
        trace_id=trace_id,
        event_type=EVENT_PDP_DECISION,
        risk_level=risk_level,
        policy_decision_id=decision_id,
        signer=signer,
    )


async def on_prepare_created(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    preparation_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    status: str,
    risk_level: str | None = None,
    trace_id: str | None = None,
    signer: CheckpointSigner | None = None,
) -> None:
    """钩子 2：Prepare 创建（准备单落库）。"""
    await _dual_write(
        session,
        project_id=project_id,
        actor_id=actor_id,
        action="PREPARE_CREATED",
        resource_type="EXECUTION_PREPARATION",
        resource_id=str(preparation_id),
        before=None,
        after={"status": status, "risk_level": risk_level},
        reason=None,
        trace_id=trace_id,
        event_type=EVENT_PREPARE_CREATED,
        risk_level=risk_level,
        signer=signer,
    )


async def on_approval_decision(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    preparation_id: uuid.UUID,
    role: str,
    decision: str,
    decided_by: uuid.UUID | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
    signer: CheckpointSigner | None = None,
) -> None:
    """钩子 3：审批决策（APPROVE / REJECT，四眼职责分离留痕）。"""
    await _dual_write(
        session,
        project_id=project_id,
        actor_id=decided_by,
        action="APPROVAL_DECISION",
        resource_type="EXECUTION_PREPARATION",
        resource_id=str(preparation_id),
        before=None,
        after={"role": role, "decision": decision, "decided_by": str(decided_by) if decided_by else None},
        reason=reason,
        trace_id=trace_id,
        event_type=EVENT_APPROVAL_DECISION,
        signer=signer,
    )


async def on_commit_issued(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    preparation_id: uuid.UUID,
    run_id: uuid.UUID,
    token_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    trace_id: str | None = None,
    signer: CheckpointSigner | None = None,
) -> None:
    """钩子 4：Commit 签发 Capability。

    只记 `token_id`（令牌标识），**不记令牌本体**——令牌是凭据，入账本即违反 AC-02。
    """
    await _dual_write(
        session,
        project_id=project_id,
        actor_id=actor_id,
        action="COMMIT_ISSUED",
        resource_type="EXECUTION_RUN",
        resource_id=str(run_id),
        before=None,
        after={"preparation_id": str(preparation_id), "token_id": str(token_id)},
        reason=None,
        trace_id=trace_id,
        event_type=EVENT_COMMIT_ISSUED,
        signer=signer,
    )


async def on_execution_transition(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    actor_id: uuid.UUID | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
    signer: CheckpointSigner | None = None,
) -> None:
    """钩子 5：执行状态迁移（before/after 记状态机前后态）。"""
    await _dual_write(
        session,
        project_id=project_id,
        actor_id=actor_id,
        action="EXECUTION_TRANSITION",
        resource_type="EXECUTION_RUN",
        resource_id=str(run_id),
        before={"status": from_status} if from_status else None,
        after={"status": to_status},
        reason=reason,
        trace_id=trace_id,
        event_type=EVENT_EXECUTION_TRANSITION,
        signer=signer,
    )


async def on_supervision_action(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    action: str,
    risk_level: str | None = None,
    rule_ids: list[str] | None = None,
    trace_id: str | None = None,
    signer: CheckpointSigner | None = None,
) -> None:
    """钩子 6：监督动作（CONTINUE / THROTTLE / PAUSE / CANCEL / QUARANTINE）。

    只记规则 ID 与动作，不记观测原始值明细以外的业务数据（脱敏聚合事实，FR-52 同口径）。
    """
    await _dual_write(
        session,
        project_id=project_id,
        actor_id=None,
        action="SUPERVISION_ACTION",
        resource_type="EXECUTION_RUN",
        resource_id=str(run_id),
        before=None,
        after={"action": action, "rule_ids": list(rule_ids or [])},
        reason=None,
        trace_id=trace_id,
        event_type=EVENT_SUPERVISION_ACTION,
        risk_level=risk_level,
        signer=signer,
    )


async def on_rollback_event(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    rollback_id: uuid.UUID,
    phase: str,
    actor_id: uuid.UUID | None = None,
    status: str | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
    signer: CheckpointSigner | None = None,
) -> None:
    """钩子 7：回滚申请 / 审批 / 执行（`phase` ∈ REQUESTED / DECIDED / EXECUTED）。

    回滚制品的项目归属经 `Pipeline` 中转（S8 遗留 2）：调用方须先解析出 `project_id`
    再调用本钩子，本模块不做归属推导——审计不该重复实现归属链（那会形成第二份真相）。
    """
    mapping = {
        "REQUESTED": EVENT_ROLLBACK_REQUESTED,
        "DECIDED": EVENT_ROLLBACK_DECIDED,
        "EXECUTED": EVENT_ROLLBACK_EXECUTED,
    }
    event_type = mapping.get(phase.upper())
    if event_type is None:
        raise ValueError(f"未知回滚阶段: {phase!r}，可选 {sorted(mapping)}")
    await _dual_write(
        session,
        project_id=project_id,
        actor_id=actor_id,
        action=f"ROLLBACK_{phase.upper()}",
        resource_type="ROLLBACK_REQUEST",
        resource_id=str(rollback_id),
        before=None,
        after={"status": status} if status else None,
        reason=reason,
        trace_id=trace_id,
        event_type=event_type,
        signer=signer,
    )


__all__ = [
    "EVENT_APPROVAL_DECISION",
    "EVENT_COMMIT_ISSUED",
    "EVENT_EXECUTION_TRANSITION",
    "EVENT_PDP_DECISION",
    "EVENT_PREPARE_CREATED",
    "EVENT_ROLLBACK_DECIDED",
    "EVENT_ROLLBACK_EXECUTED",
    "EVENT_ROLLBACK_REQUESTED",
    "EVENT_SUPERVISION_ACTION",
    "KEY_EVENT_TYPES",
    "on_approval_decision",
    "on_commit_issued",
    "on_execution_transition",
    "on_pdp_decision",
    "on_prepare_created",
    "on_rollback_event",
    "on_supervision_action",
]
