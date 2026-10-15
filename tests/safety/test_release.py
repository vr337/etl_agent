"""S9 安全进化 — 受控发布阶段机用例（FR-52）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_releases
覆盖 FR：FR-52（影子 → 小流量 1%–10% → 晋升 / 回滚；观察样本脱敏聚合）
对应 AC：AC-09（受控进化）、AC-02（观察样本零明文）
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.safety import release as release_service
from etl_agent.safety.errors import (
    SafetyCanaryOutOfRange,
    SafetyProposalNotFound,
    SafetySignatureInvalid,
    SafetyStateConflict,
)
from etl_agent.safety.release import (
    ALLOWED_STAGE_TRANSITIONS,
    CANARY_PERCENT_MAX,
    CANARY_PERCENT_MIN,
    STAGE_CANARY,
    STAGE_PROMOTION_APPROVED,
    STAGE_ROLLED_BACK,
    STAGE_SHADOW_RUNNING,
    TERMINAL_STAGES,
    ObservationSample,
    ensure_stage_transition,
    get_release_by_proposal,
    promote_to_canary,
    promote_to_production,
    rollback_release,
    start_shadow,
    validate_canary_percent,
    verify_release_signature,
)
from etl_agent.safety.signing import ReleaseSigner
from tests.helpers import SeededWorkspace, make_settings

MANIFEST = "c" * 64


def _signer() -> ReleaseSigner:
    return ReleaseSigner(make_settings().harness_signing_key_hex)


# ============================================================
# 纯校验（同步）
# ============================================================


def test_stage_machine_shape() -> None:
    """**FR-52**：阶段机不可跳跃（转移表即契约）。"""
    assert ALLOWED_STAGE_TRANSITIONS[STAGE_SHADOW_RUNNING] == frozenset(
        {STAGE_CANARY, STAGE_ROLLED_BACK}
    )
    assert ALLOWED_STAGE_TRANSITIONS[STAGE_CANARY] == frozenset(
        {STAGE_PROMOTION_APPROVED, STAGE_ROLLED_BACK}
    )
    assert TERMINAL_STAGES == frozenset({STAGE_ROLLED_BACK})


def test_ensure_stage_transition_rejects_jump() -> None:
    """SHADOW_RUNNING → PROMOTION_APPROVED 直通被拒（必须先小流量）。"""
    with pytest.raises(SafetyStateConflict, match="不可跳跃"):
        ensure_stage_transition(STAGE_SHADOW_RUNNING, STAGE_PROMOTION_APPROVED)


def test_canary_percent_boundaries() -> None:
    """**FR-52**：小流量百分比必须落在 1%–10%（边界值合法，越界拒绝）。"""
    assert CANARY_PERCENT_MIN == 1
    assert CANARY_PERCENT_MAX == 10
    assert validate_canary_percent(1) == 1
    assert validate_canary_percent(10) == 10
    assert validate_canary_percent(5) == 5


def test_canary_percent_out_of_range_rejected() -> None:
    """**FR-52**：越界即 422（不夹取——夹取会静默改变发布范围）。"""
    for bad in (0, -1, 11, 100, 50):
        with pytest.raises(SafetyCanaryOutOfRange):
            validate_canary_percent(bad)


def test_observation_sample_validates_counts() -> None:
    """**FR-52**：异常计数 ≤ 样本量（自校验）。"""
    ObservationSample(sample_size=100, anomaly_count=5).validate()
    ObservationSample(sample_size=0, anomaly_count=0).validate()
    with pytest.raises(SafetyStateConflict, match="不自洽"):
        ObservationSample(sample_size=10, anomaly_count=11).validate()
    with pytest.raises(SafetyStateConflict, match="不可为负"):
        ObservationSample(sample_size=-1, anomaly_count=0).validate()


def test_observation_sample_is_aggregate_only() -> None:
    """**AC-02**：观察样本为脱敏聚合事实——只有计数与比率，无明细。"""
    payload = ObservationSample(sample_size=100, anomaly_count=3).to_dict()
    assert set(payload) == {"sample_size", "anomaly_count", "anomaly_rate"}
    assert payload["anomaly_rate"] == 0.03


def test_observation_rate_zero_when_empty() -> None:
    """空样本异常率为 0（不除零）。"""
    assert ObservationSample(sample_size=0, anomaly_count=0).anomaly_rate == 0.0


# ============================================================
# 落库路径（异步）
# ============================================================


@pytest.mark.asyncio
async def test_start_shadow_requires_approved(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**FR-52**：非 APPROVED 提案不可启动发布（不可跳跃）。"""
    from etl_agent.safety.proposals import ProposalInput, create_proposal

    row = await create_proposal(
        db_session,
        ProposalInput(
            project_id=seeded.project_a_id,
            type="POLICY",
            title="未批准",
            created_by=seeded.engineer_a_user_id,
        ),
    )
    with pytest.raises(SafetyStateConflict, match="仅 APPROVED"):
        await start_shadow(
            db_session, proposal_id=row.id, manifest_sha256=MANIFEST, signer=_signer()
        )


@pytest.mark.asyncio
async def test_full_release_happy_path(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    approved_proposal,
) -> None:
    """**FR-52**：影子 → 小流量(5%) → 晋升（合法路径走通，清单被签名）。"""
    signer = _signer()
    release = await start_shadow(
        db_session,
        proposal_id=approved_proposal.id,
        manifest_sha256=MANIFEST,
        signer=signer,
    )
    assert release.stage == STAGE_SHADOW_RUNNING
    assert release.canary_percent is None
    assert release.signature_key_id == signer.key_id
    verify_release_signature(release, [signer.public_key_hex])

    release = await promote_to_canary(
        db_session,
        release_id=release.id,
        canary_percent=5,
        manifest_sha256=MANIFEST,
        signer=signer,
        observation=ObservationSample(sample_size=100, anomaly_count=1),
    )
    assert release.stage == STAGE_CANARY
    assert release.canary_percent == 5
    verify_release_signature(release, [signer.public_key_hex])

    release = await promote_to_production(
        db_session,
        release_id=release.id,
        manifest_sha256=MANIFEST,
        signer=signer,
        observation=ObservationSample(sample_size=1000, anomaly_count=10),
    )
    assert release.stage == STAGE_PROMOTION_APPROVED
    verify_release_signature(release, [signer.public_key_hex])


@pytest.mark.asyncio
async def test_canary_out_of_range_at_service_layer(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    approved_proposal,
) -> None:
    """**FR-52**：服务层同样拒绝越界百分比（不只路由层拦）。"""
    signer = _signer()
    release = await start_shadow(
        db_session, proposal_id=approved_proposal.id, manifest_sha256=MANIFEST, signer=signer
    )
    with pytest.raises(SafetyCanaryOutOfRange):
        await promote_to_canary(
            db_session,
            release_id=release.id,
            canary_percent=50,
            manifest_sha256=MANIFEST,
            signer=signer,
        )


@pytest.mark.asyncio
async def test_duplicate_release_conflicts(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    approved_proposal,
) -> None:
    """同一提案已有发布记录 → 409（safety_releases 一对一）。"""
    signer = _signer()
    await start_shadow(
        db_session, proposal_id=approved_proposal.id, manifest_sha256=MANIFEST, signer=signer
    )
    with pytest.raises(SafetyStateConflict, match="已有发布记录"):
        await start_shadow(
            db_session, proposal_id=approved_proposal.id, manifest_sha256=MANIFEST, signer=signer
        )


@pytest.mark.asyncio
async def test_manifest_swap_detected(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    approved_proposal,
) -> None:
    """**FR-52**：小流量阶段换清单摘要 → 签名校验失败（清单被换）。"""
    signer = _signer()
    release = await start_shadow(
        db_session, proposal_id=approved_proposal.id, manifest_sha256=MANIFEST, signer=signer
    )
    with pytest.raises(SafetySignatureInvalid, match="清单被换"):
        await promote_to_canary(
            db_session,
            release_id=release.id,
            canary_percent=5,
            manifest_sha256="d" * 64,  # 换了清单
            signer=signer,
        )


@pytest.mark.asyncio
async def test_promotion_rejected_on_high_anomaly_rate(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    approved_proposal,
) -> None:
    """**FR-52**：小流量异常率超阈值 → 拒绝晋升（须人工裁决）。"""
    signer = _signer()
    release = await start_shadow(
        db_session, proposal_id=approved_proposal.id, manifest_sha256=MANIFEST, signer=signer
    )
    release = await promote_to_canary(
        db_session,
        release_id=release.id,
        canary_percent=5,
        manifest_sha256=MANIFEST,
        signer=signer,
    )
    with pytest.raises(SafetyStateConflict, match="超过阈值"):
        await promote_to_production(
            db_session,
            release_id=release.id,
            manifest_sha256=MANIFEST,
            signer=signer,
            observation=ObservationSample(sample_size=100, anomaly_count=20),
        )


@pytest.mark.asyncio
async def test_rollback_from_shadow(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    approved_proposal,
) -> None:
    """**FR-52**：影子阶段即可回滚（ROLLED_BACK 为终态）。"""
    signer = _signer()
    release = await start_shadow(
        db_session, proposal_id=approved_proposal.id, manifest_sha256=MANIFEST, signer=signer
    )
    release = await rollback_release(
        db_session,
        release_id=release.id,
        manifest_sha256=MANIFEST,
        signer=signer,
        reason="影子异常",
    )
    assert release.stage == STAGE_ROLLED_BACK


@pytest.mark.asyncio
async def test_rollback_twice_conflicts(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    approved_proposal,
) -> None:
    """终态不可重复回滚。"""
    signer = _signer()
    release = await start_shadow(
        db_session, proposal_id=approved_proposal.id, manifest_sha256=MANIFEST, signer=signer
    )
    await rollback_release(
        db_session, release_id=release.id, manifest_sha256=MANIFEST, signer=signer
    )
    with pytest.raises(SafetyStateConflict, match="终态"):
        await rollback_release(
            db_session, release_id=release.id, manifest_sha256=MANIFEST, signer=signer
        )


@pytest.mark.asyncio
async def test_get_release_missing_404(db_session: AsyncSession) -> None:
    """不存在的发布记录 → 404。"""
    with pytest.raises(SafetyProposalNotFound):
        await release_service.get_release(db_session, uuid.uuid4())


@pytest.mark.asyncio
async def test_get_release_by_proposal_none_when_absent(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """未发布提案 → None（查询不产生副作用）。"""
    assert await get_release_by_proposal(db_session, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_start_shadow_signature_verifies_under_other_key_fails(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    approved_proposal,
) -> None:
    """**FR-52**：用不匹配的公钥验签 → 失败（签名把「批准的」与「发布的」绑定）。"""
    signer = _signer()
    release = await start_shadow(
        db_session, proposal_id=approved_proposal.id, manifest_sha256=MANIFEST, signer=signer
    )
    other = ReleaseSigner("11" * 32)
    with pytest.raises(SafetySignatureInvalid):
        verify_release_signature(release, [other.public_key_hex])
