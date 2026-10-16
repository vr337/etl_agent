"""M-J Harness 安全执行内核 — 四眼审批（T-12.6，AC-01 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J（四眼审批）/ 第 5.2 章 harness_approval_decisions / 第 6.2 章 API-09
覆盖 FR：FR-32（具名审批人对职责槽位决策；`(preparation_id, role)` 与 `(preparation_id, decided_by)` 双唯一；`evidence_sha256`）
对应 AC：AC-01（四眼职责分离拦截）

四层防线（PRD 2.3 红线 / SPEC 8「安全 · 授权与职责分离」）：
1. DB 双唯一约束（`uq_harness_approvals_prep_role` / `uq_harness_approvals_prep_decided_by`）；
2. API 层显式预检（自批 / 槽被占）→ 返回语义化错误码；
3. 责任人必须持有对应 Harness 职责（ADMIN 不隐式升级，FR-03）；
4. 申请人自批先于一切检查被拒绝（同一自然人既提交又决策）。
API 层捕获 IntegrityError 作为最后兜底——并发同槽竞争时以 DB 判定为准。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.audit.hooks import on_approval_decision
from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.errors import RoleRequired
from etl_agent.harness.errors import (
    PreparationExpired,
    PreparationNotFound,
    PreparationStateConflict,
    RoleTaken,
    SelfApproval,
)
from etl_agent.harness.prepare.service import STATUS_READY, STATUS_WAITING_APPROVAL
from etl_agent.infrastructure.models import ExecutionPreparation, HarnessApprovalDecision

DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"

STATUS_REJECTED = "REJECTED"


@dataclass(frozen=True)
class ApprovalOutcome:
    """审批决策结果。"""

    preparation_id: uuid.UUID
    role: str
    decision: str
    decided_by: uuid.UUID
    status: str
    approvals_received: int
    approvals_required: int


class ApproveService:
    """四眼审批服务：槽位唯一 + 禁自批 + 职责校验 + 计数达标后转 READY。"""

    async def decide(
        self,
        session: AsyncSession,
        *,
        preparation_id: uuid.UUID,
        project_id: uuid.UUID,
        actor: ActorContext,
        role: str,
        decision: str,
        reason: str | None = None,
        evidence_sha256: str | None = None,
    ) -> ApprovalOutcome:
        """对准备单做审批决策（API-09）。

        Args:
            session: 请求级数据库会话。
            preparation_id: 准备单。
            project_id: 隔离边界（调用方已做过成员校验）。
            actor: 当前审批人（含 Harness 职责）。
            role: 审批职责槽（DATA_APPROVER / SECURITY_APPROVER）。
            decision: APPROVE / REJECT。
            reason: 决策理由（留痕）。
            evidence_sha256: 决策证据摘要（FR-32，可缺省）。

        Returns:
            ApprovalOutcome: 决策结果与当前票数。

        Raises:
            PreparationNotFound:      准备单不存在或跨项目。
            PreparationStateConflict: 准备单非 WAITING_APPROVAL。
            RoleRequired:            actor 不具备该 Harness 职责。
            SelfApproval:            申请人自批（PRD 2.3-1）。
            RoleTaken:               职责槽已被占用（PRD 2.3-2）。
            PreparationExpired:      准备单 TTL 已过期（FR-31）。
        """
        preparation = await self._load(session, preparation_id, project_id)

        if preparation.status != STATUS_WAITING_APPROVAL:
            if preparation.status == STATUS_READY:
                raise PreparationStateConflict("准备单已满足审批票数，无需继续审批")
            raise PreparationStateConflict(f"准备单状态 {preparation.status} 不允许审批")
        if preparation.expires_at is not None and preparation.expires_at <= datetime.now(UTC):
            raise PreparationExpired("准备单已过期（TTL 24h，FR-31）")

        required_roles = list(preparation.required_roles or [])
        if role not in required_roles:
            raise RoleRequired(f"职责槽 {role} 不在本准备单要求范围内（{', '.join(required_roles) or '无'}）")
        # 具名审批人必须具备对应 Harness 职责（ADMIN 不隐式升级，FR-03）
        if role not in actor.harness_roles:
            raise RoleRequired(f"缺少 Harness 职责: {role}")

        # 申请人禁止自批：先于其他校验（PRD 2.3-1 职责分离红线）
        if actor.user_id == preparation.requested_by:
            raise SelfApproval("审批申请人不得审批自己的准备单（PRD 2.3-1）")

        existing = await self._decisions(session, preparation_id)
        if any(item.role == role for item in existing):
            raise RoleTaken(f"职责槽 {role} 已被占用：同一职责槽只能一人审批（PRD 2.3-2）")
        # 一人只能占一个审批槽（PRD 2.3-1：一人一槽）
        if any(item.decided_by == actor.user_id for item in existing):
            raise SelfApproval("同一自然人不得占用多个审批槽（PRD 2.3-1）")

        row = HarnessApprovalDecision(
            preparation_id=preparation.id,
            role=role,
            decided_by=actor.user_id,
            decision=decision,
            reason=reason,
            evidence_sha256=evidence_sha256,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError as exc:
            # DB 双唯一兜底：并发同槽 / 同人竞争时以 DB 判定为准（AC-01）
            message = str(exc.orig) if exc.orig is not None else str(exc)
            if "uq_harness_approvals_prep_decided_by" in message:
                raise SelfApproval("同一自然人不得占用多个审批槽（DB 唯一约束）") from exc
            if "uq_harness_approvals_prep_role" in message:
                raise RoleTaken("职责槽已被占用（DB 唯一约束）") from exc
            raise

        # 钩子 3（F3）：审批决策双写证据账本（APPROVE / REJECT 均留痕；
        # 随调用方路由既有事务提交，钩子只 flush 不自行 commit）
        await on_approval_decision(
            session,
            project_id=project_id,
            preparation_id=preparation.id,
            role=role,
            decision=decision,
            decided_by=actor.user_id,
            reason=reason,
        )

        if decision == DECISION_REJECT:
            preparation.status = STATUS_REJECTED
            await session.flush()
            return ApprovalOutcome(
                preparation_id=preparation.id,
                role=role,
                decision=decision,
                decided_by=actor.user_id,
                status=preparation.status,
                approvals_received=0,
                approvals_required=preparation.required_approvals or 0,
            )

        decisions = await self._decisions(session, preparation.id)
        approves = sum(1 for item in decisions if item.decision == DECISION_APPROVE)
        required = preparation.required_approvals or 0
        if approves >= required and set(required_roles or []) <= {
            item.role for item in decisions if item.decision == DECISION_APPROVE
        }:
            preparation.status = STATUS_READY
            await session.flush()
        return ApprovalOutcome(
            preparation_id=preparation.id,
            role=role,
            decision=decision,
            decided_by=actor.user_id,
            status=preparation.status,
            approvals_received=approves,
            approvals_required=required,
        )

    async def _load(
        self, session: AsyncSession, preparation_id: uuid.UUID, project_id: uuid.UUID
    ) -> ExecutionPreparation:
        """按准备单 + 项目加载（跨项目视同不存在）。"""
        result = await session.execute(
            select(ExecutionPreparation).where(
                ExecutionPreparation.id == preparation_id,
                ExecutionPreparation.project_id == project_id,
            )
        )
        preparation = result.scalars().first()
        if preparation is None:
            raise PreparationNotFound("准备单不存在或不属于当前项目")
        return preparation

    async def _decisions(
        self, session: AsyncSession, preparation_id: uuid.UUID
    ) -> list[HarnessApprovalDecision]:
        result = await session.execute(
            select(HarnessApprovalDecision).where(
                HarnessApprovalDecision.preparation_id == preparation_id
            )
        )
        return list(result.scalars().all())
