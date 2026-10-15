"""M-M 评测与安全进化 — 安全改进提案状态机（FR-51）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_improvement_proposals
覆盖 FR：FR-51（类型限定 POLICY/PROMPT/BENCHMARK；候选非可执行代码；状态机不可跳跃；仅人类安全审批人裁决）
对应 AC：AC-01（职责分离：安全维度独立审批）、AC-09

**状态机**（S9 提示词 §8.8，不可跳跃）::

    DRAFT → IN_REVIEW → APPROVED / REJECTED → RELEASED

三条红线：
1. **不可跳跃**：`submit → approve` 直通返回 409（必须先 IN_REVIEW）；
2. **类型限定**：POLICY / PROMPT / BENCHMARK，其他类型拒绝创建；
3. **仅 SECURITY_APPROVER 可裁决**（AC-01 职责分离；管理员不隐式升级）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import SafetyImprovementProposal
from etl_agent.safety.errors import SafetyProposalNotFound, SafetyStateConflict

STATUS_DRAFT = "DRAFT"
STATUS_IN_REVIEW = "IN_REVIEW"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_RELEASED = "RELEASED"

# 提案类型白名单（FR-51：类型限定）
PROPOSAL_TYPES: frozenset[str] = frozenset({"POLICY", "PROMPT", "BENCHMARK"})

# 状态机允许的转移（不可跳跃由本表强制）
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_DRAFT: frozenset({STATUS_IN_REVIEW}),
    STATUS_IN_REVIEW: frozenset({STATUS_APPROVED, STATUS_REJECTED}),
    STATUS_APPROVED: frozenset({STATUS_RELEASED}),
    STATUS_REJECTED: frozenset(),
    STATUS_RELEASED: frozenset(),
}

# 终态
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_REJECTED, STATUS_RELEASED}
)

# 裁决所需职责（FR-51 / AC-01：仅人类安全审批人）
REQUIRED_ROLE = "SECURITY_APPROVER"

# 可执行代码特征（候选必须为非可执行代码，FR-51）
_EXECUTABLE_MARKERS: tuple[str, ...] = (
    "python",
    "def ",
    "import ",
    "eval(",
    "exec(",
    "os.system",
    "subprocess",
    "__import__",
)


@dataclass(frozen=True)
class ProposalInput:
    """创建提案的输入（payload 为声明式候选，不含可执行代码）。"""

    project_id: uuid.UUID
    type: str
    title: str
    summary: str | None = None
    payload: dict[str, Any] | None = None
    benchmark_run_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None


def ensure_transition_allowed(current: str, target: str) -> None:
    """校验状态转移是否被状态机允许（不可跳跃）。

    Raises:
        SafetyStateConflict: 当前状态不允许转移到目标状态。
    """
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise SafetyStateConflict(
            f"提案状态机不允许 {current} → {target}（不可跳跃，FR-51）"
        )


def looks_executable(payload: dict[str, Any] | None) -> bool:
    """粗判候选是否含可执行代码特征（FR-51：候选须为非可执行代码）。

    这是**声明式校验**，不是沙箱——目的是拦下「把脚本塞进 payload」的明显违规，
    真正的安全边界是不执行提案内容（本模块只做状态机，不 eval / 不导入用户内容）。
    """
    if not payload:
        return False
    text = repr(payload).lower()
    return any(marker in text for marker in _EXECUTABLE_MARKERS)


async def create_proposal(
    session: AsyncSession, payload: ProposalInput
) -> SafetyImprovementProposal:
    """创建提案（状态 DRAFT；类型限定 + 非可执行校验）。

    Raises:
        SafetyStateConflict: 类型非法或候选含可执行代码。
    """
    if payload.type not in PROPOSAL_TYPES:
        raise SafetyStateConflict(
            f"提案类型非法: {payload.type!r}，可选 {sorted(PROPOSAL_TYPES)}（FR-51）"
        )
    if looks_executable(payload.payload):
        raise SafetyStateConflict("提案候选含可执行代码特征，拒绝创建（FR-51：候选非可执行）")
    row = SafetyImprovementProposal(
        project_id=payload.project_id,
        benchmark_run_id=payload.benchmark_run_id,
        type=payload.type,
        status=STATUS_DRAFT,
        title=payload.title,
        summary=payload.summary,
        payload=payload.payload,
        created_by=payload.created_by or uuid.UUID(int=0),
    )
    session.add(row)
    await session.flush()
    return row


async def get_proposal(
    session: AsyncSession, proposal_id: uuid.UUID
) -> SafetyImprovementProposal:
    """取提案，不存在即 404。"""
    row = await session.get(SafetyImprovementProposal, proposal_id)
    if row is None:
        raise SafetyProposalNotFound("安全改进提案不存在")
    return row


async def submit_proposal(
    session: AsyncSession, proposal_id: uuid.UUID
) -> SafetyImprovementProposal:
    """DRAFT → IN_REVIEW（提交评审）。"""
    row = await get_proposal(session, proposal_id)
    ensure_transition_allowed(row.status, STATUS_IN_REVIEW)
    row.status = STATUS_IN_REVIEW
    session.add(row)
    await session.flush()
    return row


async def decide_proposal(
    session: AsyncSession,
    *,
    proposal_id: uuid.UUID,
    decision: str,
    decided_by: uuid.UUID,
    actor_roles: frozenset[str],
    reason: str | None = None,
) -> SafetyImprovementProposal:
    """IN_REVIEW → APPROVED / REJECTED（仅 SECURITY_APPROVER）。

    Args:
        decision: `APPROVE` 或 `REJECT`。
        actor_roles: 调用者的 Harness 职责集合。
        reason: 裁决理由（落 `decision_reason`）。

    Raises:
        SafetyStateConflict: 状态不允许 / 裁决值非法。
        RoleRequired: 调用者无 SECURITY_APPROVER 职责（由本函数抛出，见下）。
    """
    from etl_agent.auth.errors import RoleRequired

    # 职责分离红线：ADMIN 不隐式升级——无 SECURITY_APPROVER 授予即 403（FR-51 / AC-01）
    if REQUIRED_ROLE not in actor_roles:
        raise RoleRequired(REQUIRED_ROLE)

    normalized = decision.strip().upper()
    if normalized not in ("APPROVE", "REJECT"):
        raise SafetyStateConflict(f"裁决值非法: {decision!r}，可选 APPROVE / REJECT")
    target = STATUS_APPROVED if normalized == "APPROVE" else STATUS_REJECTED

    row = await get_proposal(session, proposal_id)
    ensure_transition_allowed(row.status, target)
    row.status = target
    row.decided_by = decided_by
    row.decision = normalized
    row.decision_reason = reason
    row.decided_at = datetime.now(UTC)
    session.add(row)
    await session.flush()
    return row


async def mark_released(
    session: AsyncSession, proposal_id: uuid.UUID
) -> SafetyImprovementProposal:
    """APPROVED → RELEASED（受控发布完成后由发布服务调用）。"""
    row = await get_proposal(session, proposal_id)
    ensure_transition_allowed(row.status, STATUS_RELEASED)
    row.status = STATUS_RELEASED
    session.add(row)
    await session.flush()
    return row


async def list_proposals(
    session: AsyncSession, project_id: uuid.UUID, *, limit: int = 50
) -> list[SafetyImprovementProposal]:
    """列出项目的提案（按创建时间倒序）。"""
    result = await session.execute(
        select(SafetyImprovementProposal)
        .where(SafetyImprovementProposal.project_id == project_id)
        .order_by(SafetyImprovementProposal.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


__all__ = [
    "ALLOWED_TRANSITIONS",
    "PROPOSAL_TYPES",
    "REQUIRED_ROLE",
    "STATUS_APPROVED",
    "STATUS_DRAFT",
    "STATUS_IN_REVIEW",
    "STATUS_REJECTED",
    "STATUS_RELEASED",
    "TERMINAL_STATUSES",
    "ProposalInput",
    "create_proposal",
    "decide_proposal",
    "ensure_transition_allowed",
    "get_proposal",
    "list_proposals",
    "looks_executable",
    "mark_released",
    "submit_proposal",
]
