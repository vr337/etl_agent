"""M-H 不可变版本与设计审批 — ApprovalRequest（FR-28 设计审批）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H / 第 5.2 章 approval_requests / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-28（ApprovalRequest 绑定 artifact_sha256；APPROVE/REJECT 决策与理由留痕）
对应 AC：AC-04（不可变版本）、AC-01（职责分离：申请人禁自批）

与 Harness 四眼审批（FR-32）的分工：本模块是**设计维度**审批（单审批人 + 绑定制品摘要），
Harness 准备单审批是**执行维度**四眼审批（双唯一 + 角色槽位），两者互不替代。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.errors import RoleRequired
from etl_agent.infrastructure.models import ApprovalRequest
from etl_agent.versions.artifacts import require_frozen
from etl_agent.versions.errors import (
    ApprovalAlreadyDecided,
    ApprovalNotFound,
    ApprovalSelfForbidden,
    VersionStateConflict,
)
from etl_agent.versions.service import transition
from etl_agent.versions.state import (
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_WAITING_APPROVAL,
)

STATUS_PENDING = "PENDING"
DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"

# 设计审批可用的 Harness 职责（PRD 2.1：项目审批人 / 数据审批人 / 安全审批人）
DESIGN_APPROVER_ROLES = ("DATA_APPROVER", "SECURITY_APPROVER", "APPROVER")


@dataclass(frozen=True)
class ApprovalOutcome:
    """审批决策结果（供路由组装响应）。"""

    request: ApprovalRequest
    version_status: str


async def create_approval_request(
    session: AsyncSession,
    *,
    version_id: uuid.UUID,
    project_id: uuid.UUID,
    requested_by: uuid.UUID,
) -> ApprovalRequest:
    """为已冻结版本创建设计审批申请（VALIDATED → WAITING_APPROVAL）。

    幂等：同一版本已存在 PENDING 申请时返回该申请，不重复建单。

    Raises:
        VersionNotFound:      版本不存在或跨项目。
        ArtifactNotFrozen:    版本未冻结制品（无 artifact_sha256）。
        VersionStateConflict: 版本状态不允许提交审批。
    """
    version, _ = await require_frozen(session, version_id, project_id)

    existing = (
        await session.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.pipeline_version_id == version.id,
                ApprovalRequest.status == STATUS_PENDING,
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing

    request = ApprovalRequest(
        pipeline_version_id=version.id,
        artifact_sha256=version.artifact_sha256 or "",
        status=STATUS_PENDING,
        requested_by=requested_by,
    )
    session.add(request)
    await session.flush()
    if version.status != STATUS_WAITING_APPROVAL:
        await transition(session, version, STATUS_WAITING_APPROVAL)
    return request


async def load_approval(
    session: AsyncSession, request_id: uuid.UUID
) -> tuple[ApprovalRequest, uuid.UUID]:
    """加载审批申请及其所属项目（API 路径无 project_id，需回溯项目做鉴权）。

    Raises:
        ApprovalNotFound: 申请不存在（BIZ_404_NOT_FOUND）。
    """
    from etl_agent.infrastructure.models import Pipeline, PipelineVersion

    row = (
        await session.execute(
            select(ApprovalRequest, Pipeline.project_id)
            .join(PipelineVersion, ApprovalRequest.pipeline_version_id == PipelineVersion.id)
            .join(Pipeline, PipelineVersion.pipeline_id == Pipeline.id)
            .where(ApprovalRequest.id == request_id)
        )
    ).one_or_none()
    if row is None:
        raise ApprovalNotFound("设计审批申请不存在")
    return row[0], row[1]


async def decide_approval(
    session: AsyncSession,
    *,
    request: ApprovalRequest,
    actor: ActorContext,
    decision: str,
    reason: str | None = None,
) -> ApprovalOutcome:
    """对设计审批申请做 APPROVE/REJECT 决策（FR-28 + PRD 2.3-1 禁自批）。

    Args:
        session: 请求级数据库会话。
        request: 已加载的审批申请。
        actor: 当前 Actor（含 Harness 职责）。
        decision: APPROVE / REJECT。
        reason: 决策理由（留痕）。

    Returns:
        ApprovalOutcome: 决策后的申请与版本状态。

    Raises:
        ApprovalAlreadyDecided: 申请已决策（不可重复决策）。
        RoleRequired:           actor 不具备审批职责。
        ApprovalSelfForbidden: 申请人自批（PRD 2.3-1 红线）。
        VersionStateConflict:  版本状态不允许迁移到 APPROVED/REJECTED。
    """
    if request.status != STATUS_PENDING:
        raise ApprovalAlreadyDecided(f"审批申请已处于 {request.status}，不可重复决策")

    # 职责分离：审批人必须具备设计审批职责（ADMIN 不隐式升级，FR-03）
    if not (actor.harness_roles & set(DESIGN_APPROVER_ROLES)):
        raise RoleRequired("需要设计审批职责（DATA_APPROVER / SECURITY_APPROVER）")

    # 申请人禁止自批（PRD 2.3-1 职责分离红线）：同一自然人不得既提交又决策
    if actor.user_id == request.requested_by:
        raise ApprovalSelfForbidden("设计审批申请人不得自批（PRD 2.3-1）")

    request.decision = decision
    request.decided_by = actor.user_id
    request.reason = reason
    request.decided_at = datetime.now(UTC)
    request.status = STATUS_APPROVED if decision == DECISION_APPROVE else STATUS_REJECTED

    from etl_agent.infrastructure.models import PipelineVersion

    version = await session.get(PipelineVersion, request.pipeline_version_id)
    if version is None:
        raise VersionStateConflict("审批申请绑定的版本不存在")
    target = STATUS_APPROVED if decision == DECISION_APPROVE else STATUS_REJECTED
    await transition(session, version, target)
    return ApprovalOutcome(request=request, version_status=version.status)
