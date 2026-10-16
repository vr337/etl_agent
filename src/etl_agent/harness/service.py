"""M-J Harness 安全执行内核 — 跨子域共享服务（路由编排用）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 6.1 章 API-08/09/10
覆盖 FR：FR-31（准备单）、FR-66（项目隔离）
对应 AC：AC-05（prepare→approve→commit）、AC-12（跨项目 403）

只放路由编排所需的轻量查询；安全判决全部在 prepare / approve / commit 服务内。
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.errors import PreparationNotFound
from etl_agent.infrastructure.models import (
    ExecutionPreparation,
    HarnessApprovalDecision,
    HarnessPolicyDecision,
)


async def load_preparation_project(
    session: AsyncSession, preparation_id: uuid.UUID
) -> uuid.UUID:
    """反查准备单所属项目（API-09/10 路径无 `project_id`，需回溯项目做鉴权）。

    Raises:
        PreparationNotFound: 准备单不存在（BIZ_404_NOT_FOUND，不泄露存在性）。
    """
    row = (
        await session.execute(
            select(ExecutionPreparation.project_id).where(
                ExecutionPreparation.id == preparation_id
            )
        )
    ).one_or_none()
    if row is None:
        raise PreparationNotFound("准备单不存在")
    return row[0]


async def list_preparations_for_project(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[tuple[ExecutionPreparation, str, list[HarnessApprovalDecision]]], int]:
    """按项目列出准备单（API-08 列表；准备单自带 `project_id`，直接隔离）。

    返回每张准备单连同其 **PDP 判决**（经 `policy_decision_id` 取 `outcome`）与
    **审批槽位事实**（`harness_approval_decisions`）——前端审批台据此渲染
    「待四眼审批」卡与「申请人禁自批」判定（AC-01）。

    Args:
        session: 请求级数据库会话。
        project_id: 项目隔离边界（`execution_preparations.project_id`）。
        status: 可选状态过滤（DENIED / WAITING_APPROVAL / READY / REJECTED /
            EXPIRED / COMMITTED；非法值由路由层先行校验）。
        limit: 单页条数（路由层已 clamp 到 1..200）。
        offset: 偏移（分页）。

    Returns:
        (rows, total)：rows 为 (准备单, PDP 判决, 审批槽位列表) 列表，按
        `created_at` 倒序（同刻以 `id` 兜底）；total 为同过滤条件下的总数。
    """
    base = select(ExecutionPreparation, HarnessPolicyDecision.outcome).join(
        HarnessPolicyDecision,
        ExecutionPreparation.policy_decision_id == HarnessPolicyDecision.id,
    ).where(ExecutionPreparation.project_id == project_id)
    if status is not None:
        base = base.where(ExecutionPreparation.status == status)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    page = (
        await session.execute(
            base.order_by(
                ExecutionPreparation.created_at.desc(),
                ExecutionPreparation.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()

    preparation_ids = [row[0].id for row in page]
    approvals_by_prep: dict[uuid.UUID, list[HarnessApprovalDecision]] = {
        pid: [] for pid in preparation_ids
    }
    if preparation_ids:
        decision_rows = (
            (
                await session.execute(
                    select(HarnessApprovalDecision)
                    .where(HarnessApprovalDecision.preparation_id.in_(preparation_ids))
                    .order_by(
                        HarnessApprovalDecision.preparation_id,
                        HarnessApprovalDecision.role,
                    )
                )
            )
            .scalars()
            .all()
        )
        for decision in decision_rows:
            approvals_by_prep[decision.preparation_id].append(decision)

    rows = [
        (preparation, outcome, approvals_by_prep[preparation.id]) for preparation, outcome in page
    ]
    return rows, int(total)
