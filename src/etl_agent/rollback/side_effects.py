"""M-L 受管回滚 — 回滚 / 清理副作用协议（`ROLLBACK_DORIS` / `CLEANUP_DORIS`，FR-48）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 3.2 章 M-L / 第 6.1 章 API-13 /
第 8 章「安全 · 授权与职责分离」/ 第 8 章「数据面发布」
覆盖 FR：FR-48（受管回滚与制品清理）、FR-37（9 类副作用同协议）、FR-34（Tool Broker 唯一出口 + 防重放）
对应 AC：AC-08（审批 → Commit → Outbox → Worker → 恢复原状态）

**为什么本模块存在**：S6 落地了副作用注册清单与三类表（`harness_side_effect_preparations` /
`harness_side_effect_approvals` / `harness_side_effect_capability_grants`），但**没有实现
副作用协议的服务层**——回滚 / 清理两类副作用因此一直走不到 Outbox。本模块在 S6 的
PDP 引擎、`CapabilityIssuer`、`enqueue_command` 之上补齐这段链路，
**只复用 S6 契约、不修改**（禁止事项 7）。

协议与主执行完全同构（S6 §8.7）：

```
prepare（PDP 评级 → WAITING_APPROVAL / READY）
   → 四眼审批（(preparation_id, role) 与 (preparation_id, decided_by) 双唯一）
   → commit（审批事实重验 → 签发 Ed25519 单次 Capability → 同事务写 Outbox PENDING）
   → Worker 拉取 Outbox → 验签 + Replay Guard 消费令牌 → 执行数据面动作
```

`ROLLBACK_DORIS` / `CLEANUP_DORIS` 在 S6 注册清单中均为**破坏性**，PDP 的
`HARNESS-P0-DESTRUCTIVE-OP` 自动给出 P0 + 双人批准——这正是 PRD 2.3-3 与提示词 §8.11
「默认 P0 → 双人审批」的落地依据，本模块无需另设阈值。

**红线（禁止事项 17）**：本模块不提供任何「跳过审批直接执行」的入口；
数据面动作（drop 影子表）只由 Worker 在令牌被合法消费之后触发，
且**只碰影子表 / 错误表，永不碰正式表**（S8 不做反向 Swap）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.shadow import SHADOW_SUFFIX
from etl_agent.harness.capability.issuer import (
    DEFAULT_ALLOWED_MITIGATIONS,
    CapabilityClaims,
    CapabilityIssuer,
)
from etl_agent.harness.capability.verifier import CapabilityVerifier
from etl_agent.harness.errors import (
    PolicyDeny,
    PreparationNotFound,
    PreparationStateConflict,
)
from etl_agent.harness.pdp.engine import PdpEngine, PdpRequest, PolicyDecisionResult
from etl_agent.harness.side_effects.registry import (
    get as lookup_side_effect,
)
from etl_agent.harness.side_effects.registry import (
    is_registered,
)
from etl_agent.infrastructure.models import (
    HarnessPolicyDecision,
    HarnessSideEffectApproval,
    HarnessSideEffectCapabilityGrant,
    HarnessSideEffectPreparation,
)

if TYPE_CHECKING:
    from etl_agent.execution.shadow import ShadowTableExecutor

# 本模块承接的两系副作用（其余 7 类副作用 S8 不执行，由注册清单继续把关）
SIDE_EFFECT_ROLLBACK = "ROLLBACK_DORIS"
SIDE_EFFECT_CLEANUP = "CLEANUP_DORIS"
ROLLBACK_SIDE_EFFECTS: frozenset[str] = frozenset({SIDE_EFFECT_ROLLBACK, SIDE_EFFECT_CLEANUP})

# 准备单状态（与 S6 execution_preparations 同值）
STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"
STATUS_READY = "READY"
STATUS_COMMITTED = "COMMITTED"

# 审批决策（与 S6 同值）
DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"

# 副作用默认环境（无版本绑定，环境由调用方显式传入；缺省 prod 更保守）
DEFAULT_ENVIRONMENT = "prod"

# 副作用工具版本缺省值（注册清单只登记工具名，不登记版本，见 `side_effect_tool`）
DEFAULT_TOOL_VERSION = "1.0.0"

# 数据面动作语义（只清理影子表；S8 不做反向 Swap，永不碰正式表）
ACTION_DROP_SHADOW_TABLE = "DROP_SHADOW_TABLE"


@dataclass(frozen=True)
class SideEffectRequest:
    """副作用准备单入参（只含治理事实与引用，不含凭据与数据）。"""

    project_id: uuid.UUID
    side_effect_type: str
    requested_by: uuid.UUID
    idempotency_key: str
    reason: str
    resource_ids: tuple[str, ...] = ()
    environment: str = DEFAULT_ENVIRONMENT
    tool_name: str | None = None
    tool_version: str = "1.0.0"
    impact_report: dict[str, Any] | None = None


@dataclass(frozen=True)
class SideEffectCommitResult:
    """副作用 Commit 结果（API-13 出参的事实来源）。"""

    preparation_id: uuid.UUID
    token_id: uuid.UUID
    claims: dict[str, Any]
    expires_at: datetime
    outbox_id: uuid.UUID
    risk_level: str
    required_roles: list[str]
    required_approvals: int


def side_effect_tool(
    side_effect_type: str, tool_version: str = DEFAULT_TOOL_VERSION
) -> tuple[str, str]:
    """从注册清单取副作用登记的工具名（不在清单内即拒绝，FR-34）。

    注册清单（S6 契约，禁止修改）只登记工具名与描述，**不含版本号**；
    版本由调用方传入（默认 `DEFAULT_TOOL_VERSION`），以保证清单仍是唯一判定来源，
    同时不臆造清单里不存在的字段。
    """
    spec = lookup_side_effect(side_effect_type)
    if spec is None:
        raise PreparationStateConflict(
            f"副作用 {side_effect_type} 不在注册清单内（FR-34：以注册清单判定，非自报）"
        )
    return spec.tool_name, tool_version


async def load_side_effect_project(session: AsyncSession, preparation_id: uuid.UUID) -> uuid.UUID:
    """反查副作用准备单所属项目（API-13 路径无 project_id，需回溯项目做鉴权）。

    Raises:
        PreparationNotFound: 准备单不存在（不泄露存在性）。
    """
    row = (
        await session.execute(
            select(HarnessSideEffectPreparation.project_id).where(
                HarnessSideEffectPreparation.id == preparation_id
            )
        )
    ).one_or_none()
    if row is None:
        raise PreparationNotFound("副作用准备单不存在")
    return row[0]


async def prepare_side_effect(
    session: AsyncSession,
    *,
    request: SideEffectRequest,
    pdp_engine: PdpEngine,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> tuple[HarnessSideEffectPreparation, PolicyDecisionResult]:
    """创建副作用准备单（PDP 评级 → 指纹/影响报告 → 审批槽位，FR-37 同协议）。

    Returns:
        (准备单, PDP 裁决结果)：DENY 时不落单，直接抛 `PolicyDeny`。

    Raises:
        PolicyDeny:                PDP DENY（403，无放行通道）。
        PreparationStateConflict:   副作用未注册 / 幂等键冲突载荷不一致。
    """
    if not is_registered(request.side_effect_type):
        raise PreparationStateConflict(f"副作用 {request.side_effect_type} 不在注册清单内（FR-34）")
    existing = (
        (
            await session.execute(
                select(HarnessSideEffectPreparation).where(
                    HarnessSideEffectPreparation.project_id == request.project_id,
                    HarnessSideEffectPreparation.idempotency_key == request.idempotency_key,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        _assert_same_payload(existing, request)
        return existing, await _stored_decision(session, existing)

    tool_name = request.tool_name or side_effect_tool(request.side_effect_type)[0]
    moment = now or datetime.now(UTC)
    decision = pdp_engine.evaluate(
        PdpRequest(
            project_id=str(request.project_id),
            actor_project_id=str(request.project_id),
            environment=request.environment,
            tool_name=tool_name,
            tool_version=request.tool_version,
            command_type=request.side_effect_type,
            side_effect_type=request.side_effect_type,
            resource_ids=tuple(request.resource_ids),
        )
    )
    if decision.denied:
        raise PolicyDeny(
            f"副作用 {request.side_effect_type} 被 PDP 拒绝"
            f"（{decision.risk_level}）：无放行通道（FR-29）"
        )
    decision_row = HarnessPolicyDecision(
        project_id=request.project_id,
        outcome=decision.outcome,
        risk_level=decision.risk_level,
        rule_evidence=decision.rule_evidence,
        policy_package_id=decision.policy_package_id,
        policy_package_sha256=decision.policy_package_sha256,
    )
    session.add(decision_row)
    await session.flush()

    impact_report = request.impact_report or {}
    impact_report.setdefault("requested_input", {})
    impact_report["requested_input"].setdefault("side_effect_type", request.side_effect_type)
    impact_report["requested_input"].setdefault("resource_ids", list(request.resource_ids))
    impact_report.setdefault("reason", request.reason)

    preparation = HarnessSideEffectPreparation(
        project_id=request.project_id,
        policy_decision_id=decision_row.id,
        side_effect_type=request.side_effect_type,
        resource_ids=list(request.resource_ids),
        status=(STATUS_READY if decision.required_approvals == 0 else STATUS_WAITING_APPROVAL),
        risk_level=decision.risk_level,
        environment=request.environment,
        tool_name=tool_name,
        tool_version=request.tool_version,
        input_fingerprint=None,
        resource_fingerprint=None,
        impact_report=impact_report,
        rollback_plan={},
        supervision_contract={},
        required_roles=decision.required_roles,
        required_approvals=decision.required_approvals,
        idempotency_key=request.idempotency_key,
        requested_by=request.requested_by,
        expires_at=_resolve_expires_at(ttl_seconds, moment),
    )
    session.add(preparation)
    await session.flush()
    return preparation, decision


async def decide_side_effect(
    session: AsyncSession,
    *,
    preparation_id: uuid.UUID,
    role: str,
    decision: str,
    decided_by: uuid.UUID,
    reason: str | None = None,
    evidence_sha256: str | None = None,
) -> HarnessSideEffectApproval:
    """副作用四眼审批决策（双唯一由 DB 约束保证，同 S6）。

    Args:
        role: 审批槽位（DATA_APPROVER / SECURITY_APPROVER 等）。
        decision: APPROVE / REJECT。
        decided_by: 审批人（与申请人不同，DB 唯一约束兜底）。

    Raises:
        PreparationStateConflict: 准备单状态不允许审批。
    """
    preparation = await _load(session, preparation_id)
    if preparation.status not in {STATUS_WAITING_APPROVAL, STATUS_READY}:
        raise PreparationStateConflict(f"副作用准备单状态 {preparation.status}，不可审批（FR-37）")
    approval = HarnessSideEffectApproval(
        preparation_id=preparation.id,
        role=role,
        decided_by=decided_by,
        decision=decision,
        reason=reason,
        evidence_sha256=evidence_sha256,
    )
    session.add(approval)
    await session.flush()
    await _refresh_status(session, preparation)
    return approval


async def commit_side_effect(
    session: AsyncSession,
    *,
    preparation_id: uuid.UUID,
    project_id: uuid.UUID,
    operator_user_id: uuid.UUID,
    issuer: CapabilityIssuer,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> tuple[SideEffectCommitResult, str]:
    """Commit 副作用准备单：审批事实重验 → 签发单次 Capability → 同事务写 Outbox（FR-37）。

    返回 `(结果, 令牌本体)`：令牌本体只在本返回值中出现一次，由调用方交付 Worker 通道
    （Redis 单次读，库中只存 `token_sha256`，FR-33）。

    Raises:
        PreparationStateConflict: 状态不允许 / 审批槽位或票数不足。
        PreparationNotFound:     准备单不存在或跨项目。
    """
    from etl_agent.harness.broker.tool_broker import enqueue_command

    preparation = await _load_for_project(session, preparation_id, project_id)
    _assert_committable(preparation)
    await _reverify_approvals(session, preparation)

    claims = CapabilityClaims(
        tool_name=preparation.tool_name or "",
        tool_version=preparation.tool_version or "",
        subject=str(operator_user_id),
        environment=preparation.environment or DEFAULT_ENVIRONMENT,
        artifact_sha256="",
        supervision_contract_sha256="",
        classification_report_sha256="",
        allowed_mitigations=list(DEFAULT_ALLOWED_MITIGATIONS),
        project_id=str(project_id),
        preparation_id=str(preparation.id),
        pipeline_version_id="",
    )
    moment = now or datetime.now(UTC)
    capability = issuer.issue(claims, now=moment, ttl_seconds=ttl_seconds)

    outbox = await enqueue_command(
        session,
        project_id=project_id,
        command_type=preparation.side_effect_type,
        payload={
            "preparation_id": str(preparation.id),
            "side_effect_type": preparation.side_effect_type,
            "resource_ids": list(preparation.resource_ids or []),
            "targets": _targets_of(preparation),
            "allowed_mitigations": list(DEFAULT_ALLOWED_MITIGATIONS),
        },
        token_id=capability.token_id,
    )
    session.add(
        HarnessSideEffectCapabilityGrant(
            token_id=capability.token_id,
            preparation_id=preparation.id,
            token_sha256=capability.token_sha256,
            claims=capability.claims,
            expires_at=capability.expires_at,
        )
    )
    preparation.status = STATUS_COMMITTED
    preparation.committed_by = operator_user_id
    preparation.committed_at = moment
    await session.flush()

    result = SideEffectCommitResult(
        preparation_id=preparation.id,
        token_id=capability.token_id,
        claims=capability.claims,
        expires_at=capability.expires_at,
        outbox_id=outbox.id,
        risk_level=preparation.risk_level,
        required_roles=list(preparation.required_roles or []),
        required_approvals=preparation.required_approvals or 0,
    )
    return result, capability.token


async def verify_and_consume_side_effect(
    session: AsyncSession,
    *,
    token_id: uuid.UUID,
    token: str,
    public_keys: list[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Worker 侧验签 + 防重放消费（副作用令牌，幂等于主执行路径）。

    `HarnessSideEffectCapabilityGrant` 与主令牌表结构不同（无 `run_id`），
    故用同口径的条件 UPDATE 消费（见 `_consume_grant`）——
    原子性仍然是**数据库判据**（`WHERE consumed_at IS NULL`），非进程内判据。

    Raises:
        CapabilityInvalid:  签名 / claims 绑定校验失败。
        CapabilityReplayed: 令牌已被消费或与登记摘要不一致（FR-34）。
    """
    grant = (
        (
            await session.execute(
                select(HarnessSideEffectCapabilityGrant).where(
                    HarnessSideEffectCapabilityGrant.token_id == token_id
                )
            )
        )
        .scalars()
        .first()
    )
    if grant is None:
        from etl_agent.harness.errors import CapabilityReplayed

        raise CapabilityReplayed("副作用令牌未登记（token_id 不存在）")

    claims = CapabilityVerifier(public_keys).verify_against(
        token, now=now or datetime.now(UTC), expected=dict(grant.claims or {})
    )
    await _consume_grant(session, token_id=token_id, token_sha256=grant.token_sha256, now=now)
    return claims


async def _consume_grant(
    session: AsyncSession,
    *,
    token_id: uuid.UUID,
    token_sha256: str,
    now: datetime | None = None,
) -> None:
    """副作用令牌的一次性消费（条件 UPDATE，与 S6 `ReplayGuard` 同语义）。

    S6 的 `ReplayGuard.consume` 硬编码 `HarnessCapabilityGrant`（主令牌表），
    而副作用令牌落在 `harness_side_effect_capability_grants`（结构不同：无 `run_id`）。
    为不修改 S6 契约（禁止事项 7），此处以**同一套条件 UPDATE 判据**
    （`WHERE token_id=... AND consumed_at IS NULL`，rowcount=0 即已被消费）
    消费副作用令牌——原子性仍是数据库判据，非进程内判据。
    """
    from sqlalchemy import CursorResult, update

    from etl_agent.harness.errors import CapabilityReplayed

    result = await session.execute(
        update(HarnessSideEffectCapabilityGrant)
        .where(
            HarnessSideEffectCapabilityGrant.token_id == token_id,
            HarnessSideEffectCapabilityGrant.token_sha256 == token_sha256,
            HarnessSideEffectCapabilityGrant.consumed_at.is_(None),
        )
        .values(consumed_at=now or datetime.now(UTC))
    )
    affected: int = getattr(result, "rowcount", 0) or 0
    if affected != 1:
        raise CapabilityReplayed("副作用令牌已被消费（FR-34）")
    _ = CursorResult  # 保持 import 与 S6 口径一致（类型注解语义）


def _targets_of(preparation: HarnessSideEffectPreparation) -> dict[str, Any]:
    """从准备单影响报告取发布目标（影子表 / 正式表 / 错误表）。"""
    report = preparation.impact_report if isinstance(preparation.impact_report, dict) else {}
    targets = report.get("targets")
    return targets if isinstance(targets, dict) else {}


def _resolve_expires_at(ttl_seconds: int | None, moment: datetime) -> datetime:
    """准备单到期时刻（默认 24h，夹在 300s–7d，与 S6 `resolve_expires_at` 同口径）。

    `resolve_expires_at` 返回的是**绝对时刻**（`now + ttl`），此处直接透传，
    避免重复累加导致 `datetime + datetime` 类型错误。
    """
    from etl_agent.harness.prepare.service import resolve_expires_at

    return resolve_expires_at(ttl_seconds, moment)


async def _load(session: AsyncSession, preparation_id: uuid.UUID) -> HarnessSideEffectPreparation:
    preparation = await session.get(HarnessSideEffectPreparation, preparation_id)
    if preparation is None:
        raise PreparationNotFound("副作用准备单不存在")
    return preparation


async def _load_for_project(
    session: AsyncSession, preparation_id: uuid.UUID, project_id: uuid.UUID
) -> HarnessSideEffectPreparation:
    preparation = await session.get(HarnessSideEffectPreparation, preparation_id)
    if preparation is None or preparation.project_id != project_id:
        raise PreparationNotFound("副作用准备单不存在或不属于当前项目")
    return preparation


def _assert_same_payload(
    existing: HarnessSideEffectPreparation, request: SideEffectRequest
) -> None:
    """同幂等键不同载荷 → 409（幂等键必须绑定确定载荷，FR-31）。"""
    if (
        existing.side_effect_type != request.side_effect_type
        or existing.environment != request.environment
        or list(existing.resource_ids or []) != list(request.resource_ids)
    ):
        raise PreparationStateConflict("同一幂等键已用于不同载荷的副作用准备单（FR-31）")


def _assert_committable(preparation: HarnessSideEffectPreparation) -> None:
    """Commit 前置状态机（重复 Commit / 未就绪均拒绝，与 S6 同口径）。"""
    from etl_agent.harness.errors import ApprovalInsufficient

    if preparation.status == STATUS_COMMITTED:
        raise PreparationStateConflict(
            "副作用准备单已提交：单次令牌已签发（FR-33 不可重复签发的语义）"
        )
    if preparation.status == STATUS_WAITING_APPROVAL:
        raise ApprovalInsufficient(
            f"副作用准备单仍在等待审批：需 {preparation.required_approvals} 票（FR-37）"
        )
    if preparation.status != STATUS_READY:
        raise PreparationStateConflict(f"副作用准备单状态 {preparation.status}：不可提交（FR-37）")


async def _stored_decision(
    session: AsyncSession, preparation: HarnessSideEffectPreparation
) -> PolicyDecisionResult:
    """重放既有准备单时从决策行还原裁决结果（不重复请求 PDP）。"""
    row = await session.get(HarnessPolicyDecision, preparation.policy_decision_id)
    if row is None:
        raise PreparationStateConflict("副作用准备单缺少 PDP 决策记录")
    return PolicyDecisionResult(
        outcome=row.outcome,
        risk_level=row.risk_level,
        rule_evidence=list(row.rule_evidence or []),
        required_roles=list(preparation.required_roles or []),
        required_approvals=preparation.required_approvals or 0,
        policy_package_id=row.policy_package_id or "",
        policy_package_sha256=row.policy_package_sha256 or "",
    )


async def _refresh_status(session: AsyncSession, preparation: HarnessSideEffectPreparation) -> None:
    """审批后重算准备单状态：REJECT → REJECTED；票数与槽位达标 → READY（与 S6 同口径）。

    S6 的审批服务在落审批行后推进状态；副作用准备单沿用同一契约，
    否则 Commit 前置状态机永远停在 WAITING_APPROVAL，链路走不到 Outbox。
    """
    rows = (
        (
            await session.execute(
                select(HarnessSideEffectApproval).where(
                    HarnessSideEffectApproval.preparation_id == preparation.id
                )
            )
        )
        .scalars()
        .all()
    )
    if any(item.decision == DECISION_REJECT for item in rows):
        preparation.status = "REJECTED"
        await session.flush()
        return
    approves = [item for item in rows if item.decision == DECISION_APPROVE]
    required_roles = set(preparation.required_roles or [])
    approved_roles = {item.role for item in approves}
    if len(approves) >= (preparation.required_approvals or 0) and required_roles <= approved_roles:
        preparation.status = STATUS_READY
        await session.flush()


async def _reverify_approvals(
    session: AsyncSession, preparation: HarnessSideEffectPreparation
) -> None:
    """服务端重验审批事实（槽位全覆盖 + 票数达标 + 无 REJECT 混入）。"""
    from etl_agent.harness.errors import ApprovalInsufficient

    rows = (
        (
            await session.execute(
                select(HarnessSideEffectApproval).where(
                    HarnessSideEffectApproval.preparation_id == preparation.id
                )
            )
        )
        .scalars()
        .all()
    )
    approves = [item for item in rows if item.decision == DECISION_APPROVE]
    if any(item.decision == DECISION_REJECT for item in rows):
        raise PreparationStateConflict("存在 REJECT 决策：不得提交（FR-32）")
    required_roles = set(preparation.required_roles or [])
    missing = sorted(required_roles - {item.role for item in approves})
    if missing or len(approves) < (preparation.required_approvals or 0):
        detail = f"缺少审批槽 {', '.join(missing)}" if missing else "审批票数不足"
        raise ApprovalInsufficient(f"{detail}（需票数 {preparation.required_approvals}，FR-33）")


async def execute_side_effect_action(
    executor: ShadowTableExecutor,
    *,
    action: str,
    shadow_table: str | None,
) -> str:
    """执行数据面动作（当前只有 `DROP_SHADOW_TABLE`；永不碰正式表）。

    Args:
        executor: 影子表执行通道（生产 Doris / 测试 Logging）。
        action: 动作语义。
        shadow_table: 目标影子表名。

    Returns:
        str: 实际执行的动作描述（供事件流留痕，不含凭据）。

    Raises:
        PreparationStateConflict: 未知动作或缺少目标表。
    """
    if action != ACTION_DROP_SHADOW_TABLE:
        raise PreparationStateConflict(f"未知副作用动作 {action}（FR-46：动作白名单外拒绝）")
    if not shadow_table:
        raise PreparationStateConflict("未提供影子表名，拒绝执行清理（空目标即拒绝）")
    if not shadow_table.endswith(SHADOW_SUFFIX):
        raise PreparationStateConflict(
            f"目标 {shadow_table} 不是影子表（拒绝清理非影子表，FR-48 红线）"
        )
    target_table = shadow_table[: -len(SHADOW_SUFFIX)]
    if not target_table:
        raise PreparationStateConflict("影子表名缺少目标表前缀，拒绝执行清理")
    await executor.drop_shadow_table(target_table)
    return f"{ACTION_DROP_SHADOW_TABLE}({shadow_table})"


__all__ = [
    "ACTION_DROP_SHADOW_TABLE",
    "DECISION_APPROVE",
    "DECISION_REJECT",
    "DEFAULT_ENVIRONMENT",
    "DEFAULT_TOOL_VERSION",
    "ROLLBACK_SIDE_EFFECTS",
    "SIDE_EFFECT_CLEANUP",
    "SIDE_EFFECT_ROLLBACK",
    "STATUS_COMMITTED",
    "STATUS_READY",
    "STATUS_WAITING_APPROVAL",
    "SideEffectCommitResult",
    "SideEffectRequest",
    "commit_side_effect",
    "decide_side_effect",
    "execute_side_effect_action",
    "load_side_effect_project",
    "prepare_side_effect",
    "side_effect_tool",
    "verify_and_consume_side_effect",
]
