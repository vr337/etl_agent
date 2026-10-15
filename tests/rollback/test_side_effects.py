"""回滚 / 清理副作用协议用例（T-16.15，AC-08 核心）。

覆盖：`ROLLBACK_DORIS` / `CLEANUP_DORIS` 走 S6 Harness 协议
（prepare → 四眼审批 → commit → Outbox → Worker 消费），
副作用类型以**注册清单**判定（FR-34，非自报），
未审批不得 Commit、单次令牌不可重复签发、数据面动作永不碰正式表。

这些是 S8 回滚链路的红线：任何「跳过审批直接 drop 影子表」的路径都不允许存在。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.shadow import LoggingShadowTableExecutor
from etl_agent.harness.capability.issuer import CapabilityIssuer
from etl_agent.harness.errors import (
    ApprovalInsufficient,
    CapabilityReplayed,
    PreparationNotFound,
    PreparationStateConflict,
)
from etl_agent.harness.pdp.engine import PdpEngine
from etl_agent.harness.side_effects.registry import is_registered
from etl_agent.infrastructure.models import (
    HarnessCommandOutbox,
    HarnessSideEffectCapabilityGrant,
    HarnessSideEffectPreparation,
)
from etl_agent.rollback.side_effects import (
    ACTION_DROP_SHADOW_TABLE,
    DECISION_APPROVE,
    DECISION_REJECT,
    DEFAULT_TOOL_VERSION,
    SIDE_EFFECT_CLEANUP,
    SIDE_EFFECT_ROLLBACK,
    SideEffectRequest,
    commit_side_effect,
    decide_side_effect,
    execute_side_effect_action,
    load_side_effect_project,
    prepare_side_effect,
    side_effect_tool,
    verify_and_consume_side_effect,
)
from tests.harness.conftest import (  # noqa: F401  -- 复用 S6 职责夹具（四眼审批）
    harness_roles,
)
from tests.helpers import SeededWorkspace, make_settings


@pytest.fixture
def pdp_engine() -> PdpEngine:
    """PDP 引擎（策略包来自仓库交付物，测试不新增密钥材料，AC-02）。"""
    from etl_agent.harness.policy.package import load_package

    settings = make_settings()
    package = load_package(
        settings.policy_package_path,
        settings.policy_package_signature_path,
        settings.harness_public_key_list,
    )
    return PdpEngine(package)


@pytest.fixture
def issuer() -> CapabilityIssuer:
    """单次能力令牌签发器（签名私钥取自 infra/.env 的人类配置）。"""
    return CapabilityIssuer(make_settings().harness_signing_key_hex)


def _request(
    seeded: SeededWorkspace,
    *,
    side_effect_type: str = SIDE_EFFECT_ROLLBACK,
    idempotency_key: str | None = None,
    resource_ids: tuple[str, ...] = ("orders_wide_shadow",),
) -> SideEffectRequest:
    return SideEffectRequest(
        project_id=seeded.project_a_id,
        side_effect_type=side_effect_type,
        requested_by=seeded.engineer_a_user_id,
        idempotency_key=idempotency_key or f"rb-{uuid.uuid4().hex[:10]}",
        reason="测试：受管回滚影子表",
        resource_ids=resource_ids,
    )


# ----------------------------------------------------------------------------
# 注册清单判定（FR-34）
# ----------------------------------------------------------------------------


def test_rollback_side_effects_registered() -> None:
    """ROLLBACK_DORIS / CLEANUP_DORIS 在 S6 注册清单内（Tool Broker 唯一判定来源）。"""
    assert is_registered(SIDE_EFFECT_ROLLBACK)
    assert is_registered(SIDE_EFFECT_CLEANUP)
    # 注册清单只登记工具名；版本由 DEFAULT_TOOL_VERSION 补齐（不臆造清单字段）
    assert side_effect_tool(SIDE_EFFECT_ROLLBACK) == ("doris.rollback", DEFAULT_TOOL_VERSION)
    assert side_effect_tool(SIDE_EFFECT_CLEANUP) == ("doris.cleanup", DEFAULT_TOOL_VERSION)


def test_unregistered_side_effect_rejected() -> None:
    """清单外的副作用名（含自报的「直接 drop 正式表」）→ 状态冲突（FR-34）。"""
    with pytest.raises(PreparationStateConflict):
        side_effect_tool("DROP_PROD_TABLE")


# ----------------------------------------------------------------------------
# prepare（PDP 评级）
# ----------------------------------------------------------------------------


async def test_prepare_destructive_requires_dual_approval(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """ROLLBACK_DORIS 是破坏性副作用 → PDP 默认 P0 + 双人审批槽（PRD 2.3-3）。"""
    preparation, decision = await prepare_side_effect(
        db_session, request=_request(seeded), pdp_engine=pdp_engine
    )
    assert decision.risk_level == "P0"
    assert decision.required_approvals == 2
    assert preparation.status == "WAITING_APPROVAL"
    assert sorted(preparation.required_roles) == sorted(decision.required_roles)
    assert set(preparation.required_roles) == {"DATA_APPROVER", "SECURITY_APPROVER"}


async def test_prepare_idempotent_same_payload(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """同幂等键 + 同载荷 → 复用同一准备单（FR-31）。"""
    request = _request(seeded)
    first, _ = await prepare_side_effect(db_session, request=request, pdp_engine=pdp_engine)
    second, _ = await prepare_side_effect(db_session, request=request, pdp_engine=pdp_engine)
    assert first.id == second.id


async def test_prepare_idempotent_key_conflict_on_diff_payload(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """同幂等键 + 不同资源 → 409（幂等键必须绑定确定载荷）。"""
    request = _request(seeded, idempotency_key="dup-key")
    await prepare_side_effect(db_session, request=request, pdp_engine=pdp_engine)
    with pytest.raises(PreparationStateConflict):
        await prepare_side_effect(
            db_session,
            request=SideEffectRequest(
                project_id=seeded.project_a_id,
                side_effect_type=SIDE_EFFECT_ROLLBACK,
                requested_by=seeded.engineer_a_user_id,
                idempotency_key="dup-key",
                reason="载荷不一致",
                resource_ids=("another_shadow",),
            ),
            pdp_engine=pdp_engine,
        )


# ----------------------------------------------------------------------------
# 四眼审批
# ----------------------------------------------------------------------------


async def test_approval_state_conflict_after_commit(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    pdp_engine: PdpEngine,
    issuer: CapabilityIssuer,
) -> None:
    """已 Commit 的准备单不可再审批（终端态）。"""
    preparation, _ = await prepare_side_effect(
        db_session, request=_request(seeded), pdp_engine=pdp_engine
    )
    await _approve_all(db_session, preparation, seeded)
    await commit_side_effect(
        db_session,
        preparation_id=preparation.id,
        project_id=seeded.project_a_id,
        operator_user_id=seeded.admin_b_user_id,
        issuer=issuer,
    )
    with pytest.raises(PreparationStateConflict):
        await decide_side_effect(
            db_session,
            preparation_id=preparation.id,
            role="DATA_APPROVER",
            decision=DECISION_APPROVE,
            decided_by=seeded.outsider_user_id,
        )


# ----------------------------------------------------------------------------
# Commit（审批事实重验 + 单次令牌 + Outbox）
# ----------------------------------------------------------------------------


async def test_commit_requires_approvals(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    pdp_engine: PdpEngine,
    issuer: CapabilityIssuer,
) -> None:
    """未审批即 Commit → 审批票数不足（FR-33；红线：不得跳过审批直接执行）。"""
    preparation, _ = await prepare_side_effect(
        db_session, request=_request(seeded), pdp_engine=pdp_engine
    )
    with pytest.raises(ApprovalInsufficient):
        await commit_side_effect(
            db_session,
            preparation_id=preparation.id,
            project_id=seeded.project_a_id,
            operator_user_id=seeded.admin_b_user_id,
            issuer=issuer,
        )


async def test_commit_rejects_mixed_reject_decision(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    pdp_engine: PdpEngine,
    issuer: CapabilityIssuer,
) -> None:
    """审批中出现 REJECT → 不得 Commit（FR-32）。"""
    preparation, _ = await prepare_side_effect(
        db_session, request=_request(seeded), pdp_engine=pdp_engine
    )
    await decide_side_effect(
        db_session,
        preparation_id=preparation.id,
        role="DATA_APPROVER",
        decision=DECISION_APPROVE,
        decided_by=seeded.admin_a_user_id,
    )
    await decide_side_effect(
        db_session,
        preparation_id=preparation.id,
        role="SECURITY_APPROVER",
        decision=DECISION_REJECT,
        decided_by=seeded.admin_b_user_id,
    )
    with pytest.raises(PreparationStateConflict):
        await commit_side_effect(
            db_session,
            preparation_id=preparation.id,
            project_id=seeded.project_a_id,
            operator_user_id=seeded.admin_b_user_id,
            issuer=issuer,
        )


async def test_side_effect_harness_protocol(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    pdp_engine: PdpEngine,
    issuer: CapabilityIssuer,
) -> None:
    """**AC-08 核心**：prepare → 四眼审批 → commit → Outbox → 令牌消费（同主执行协议）。

    断言链路四要素：
    1. Commit 返回单次令牌本体（只此一次出现）；
    2. 同事务写 Outbox PENDING，命令类型即副作用类型（Worker 据此路由）；
    3. 库中只存 token_sha256，不存令牌本体（AC-02 零明文）；
    4. 令牌经验签 + Replay Guard 可被合法消费一次，二次消费被拒（FR-34）。
    """
    preparation, _ = await prepare_side_effect(
        db_session, request=_request(seeded), pdp_engine=pdp_engine
    )
    await _approve_all(db_session, preparation, seeded)
    result, token = await commit_side_effect(
        db_session,
        preparation_id=preparation.id,
        project_id=seeded.project_a_id,
        operator_user_id=seeded.admin_b_user_id,
        issuer=issuer,
    )

    assert token and "." in token  # 令牌本体 = payload.json + 签名（只此一次返回）
    assert result.preparation_id == preparation.id

    outbox = (await db_session.execute(select(HarnessCommandOutbox))).scalars().all()
    assert len(outbox) == 1
    assert outbox[0].command_type == SIDE_EFFECT_ROLLBACK
    assert outbox[0].status == "PENDING"
    assert outbox[0].payload["token_id"] == str(result.token_id)
    assert outbox[0].payload["side_effect_type"] == SIDE_EFFECT_ROLLBACK

    grant = (await db_session.execute(select(HarnessSideEffectCapabilityGrant))).scalars().one()
    assert grant.token_sha256 != token  # 库里只有摘要
    assert token not in str(grant.claims)

    claims = await verify_and_consume_side_effect(
        db_session,
        token_id=result.token_id,
        token=token,
        public_keys=_public_keys(),
    )
    assert claims["preparation_id"] == str(preparation.id)

    with pytest.raises(CapabilityReplayed):
        await verify_and_consume_side_effect(
            db_session,
            token_id=result.token_id,
            token=token,
            public_keys=_public_keys(),
        )


async def test_commit_twice_rejected(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    pdp_engine: PdpEngine,
    issuer: CapabilityIssuer,
) -> None:
    """重复 Commit → 状态冲突（单次令牌不可重复签发，FR-33）。"""
    preparation, _ = await prepare_side_effect(
        db_session, request=_request(seeded), pdp_engine=pdp_engine
    )
    await _approve_all(db_session, preparation, seeded)
    await commit_side_effect(
        db_session,
        preparation_id=preparation.id,
        project_id=seeded.project_a_id,
        operator_user_id=seeded.admin_b_user_id,
        issuer=issuer,
    )
    with pytest.raises(PreparationStateConflict):
        await commit_side_effect(
            db_session,
            preparation_id=preparation.id,
            project_id=seeded.project_a_id,
            operator_user_id=seeded.admin_b_user_id,
            issuer=issuer,
        )


async def test_commit_cross_project_not_found(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    pdp_engine: PdpEngine,
    issuer: CapabilityIssuer,
) -> None:
    """跨项目 Commit → 准备单不存在（不泄露存在性，AC-12）。"""
    preparation, _ = await prepare_side_effect(
        db_session, request=_request(seeded), pdp_engine=pdp_engine
    )
    await _approve_all(db_session, preparation, seeded)
    with pytest.raises(PreparationNotFound):
        await commit_side_effect(
            db_session,
            preparation_id=preparation.id,
            project_id=seeded.project_b_id,
            operator_user_id=seeded.admin_b_user_id,
            issuer=issuer,
        )


async def test_load_side_effect_project(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """按准备单反查项目（API-13 路径无 project_id 时的鉴权锚点）。"""
    preparation, _ = await prepare_side_effect(
        db_session, request=_request(seeded), pdp_engine=pdp_engine
    )
    assert await load_side_effect_project(db_session, preparation.id) == seeded.project_a_id
    with pytest.raises(PreparationNotFound):
        await load_side_effect_project(db_session, uuid.uuid4())


# ----------------------------------------------------------------------------
# 数据面动作（永不碰正式表）
# ----------------------------------------------------------------------------


async def test_execute_drops_only_shadow_table() -> None:
    """执行清理只 drop 影子表，回传动作描述（供事件流留痕）。"""
    executor = LoggingShadowTableExecutor()
    action = await execute_side_effect_action(
        executor, action=ACTION_DROP_SHADOW_TABLE, shadow_table="orders_wide_shadow"
    )
    assert action == "DROP_SHADOW_TABLE(orders_wide_shadow)"
    assert executor.dropped == ["orders_wide_shadow"]


async def test_execute_rejects_unknown_action() -> None:
    """动作白名单外的语义（如 DROP 正式表）→ 拒绝（FR-46）。"""
    executor = LoggingShadowTableExecutor()
    with pytest.raises(PreparationStateConflict):
        await execute_side_effect_action(
            executor, action="DROP_TARGET_TABLE", shadow_table="orders_wide"
        )
    assert executor.dropped == []


async def test_execute_rejects_empty_target() -> None:
    """未提供影子表名 → 拒绝（空目标即拒绝，避免误删）。"""
    executor = LoggingShadowTableExecutor()
    with pytest.raises(PreparationStateConflict):
        await execute_side_effect_action(
            executor, action=ACTION_DROP_SHADOW_TABLE, shadow_table=None
        )
    assert executor.dropped == []


# ----------------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------------


async def _approve_all(
    db_session: AsyncSession,
    preparation: HarnessSideEffectPreparation,
    seeded: SeededWorkspace,
) -> None:
    """按准备单要求的槽位逐个签发 APPROVE（两位不同自然人，AC-01）。"""
    actors = {
        "DATA_APPROVER": seeded.admin_a_user_id,
        "SECURITY_APPROVER": seeded.admin_b_user_id,
    }
    for role in preparation.required_roles or []:
        await decide_side_effect(
            db_session,
            preparation_id=preparation.id,
            role=role,
            decision=DECISION_APPROVE,
            decided_by=actors[role],
        )
    await db_session.flush()


def _public_keys() -> list[str]:
    """验签公钥（取自 infra/.env 的人类配置，测试不新增密钥，AC-02）。"""
    from tests.helpers import make_settings

    return make_settings().harness_public_key_list
