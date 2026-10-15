"""M-M 评测与安全进化 — 受控发布（影子 → 小流量 1%–10% → 晋升 / 回滚）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_releases /
第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-52（影子验证 → 小流量 → 晋升授权 / 回滚；发布清单 Ed25519 签名；
观察样本为脱敏聚合事实）
对应 AC：AC-09（受控进化）、AC-02（观察样本零明文）

**阶段机**（S9 提示词 §8.9，顺序不可跳）::

    SHADOW_RUNNING → CANARY(1%–10%) → PROMOTION_APPROVED
                                   ↘ ROLLED_BACK（异常时）

三条红线：
1. **必须先影子后小流量**：`SHADOW_RUNNING → CANARY` 之外的入口一律 409；
2. **小流量百分比 1%–10%**（越界即 `BIZ_422_SAFETY_CANARY_OUT_OF_RANGE`）；
3. **观察样本为脱敏聚合事实**：只回计数（`anomaly_count` / `sample_size`），
   且**异常计数 ≤ 样本量**自校验（FR-52 明列）——否则样本是假的。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import SafetyRelease
from etl_agent.safety import proposals as proposal_service
from etl_agent.safety.errors import (
    SafetyCanaryOutOfRange,
    SafetyProposalNotFound,
    SafetySignatureInvalid,
    SafetyStateConflict,
)
from etl_agent.safety.signing import (
    ReleaseManifest,
    ReleaseSigner,
    require_valid_release,
)

STAGE_SHADOW_RUNNING = "SHADOW_RUNNING"
STAGE_CANARY = "CANARY"
STAGE_PROMOTION_APPROVED = "PROMOTION_APPROVED"
STAGE_ROLLED_BACK = "ROLLED_BACK"

# 小流量百分比边界（SPEC FR-52 固定：1%–10%）
CANARY_PERCENT_MIN = 1
CANARY_PERCENT_MAX = 10

# 阶段转移白名单（不可跳跃）
ALLOWED_STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    STAGE_SHADOW_RUNNING: frozenset({STAGE_CANARY, STAGE_ROLLED_BACK}),
    STAGE_CANARY: frozenset({STAGE_PROMOTION_APPROVED, STAGE_ROLLED_BACK}),
    STAGE_PROMOTION_APPROVED: frozenset({STAGE_ROLLED_BACK}),
    STAGE_ROLLED_BACK: frozenset(),
}

TERMINAL_STAGES: frozenset[str] = frozenset({STAGE_ROLLED_BACK})


@dataclass(frozen=True)
class ObservationSample:
    """发布观察样本（**脱敏聚合事实**，FR-52）。"""

    sample_size: int
    anomaly_count: int

    def validate(self) -> None:
        """自校验：异常计数不得超过样本量（FR-52「异常计数 ≤ 样本量」）。"""
        if self.sample_size < 0 or self.anomaly_count < 0:
            raise SafetyStateConflict("观察样本计数不可为负")
        if self.anomaly_count > self.sample_size:
            raise SafetyStateConflict(
                f"观察样本不自洽：异常计数 {self.anomaly_count} > 样本量 {self.sample_size}"
            )

    @property
    def anomaly_rate(self) -> float:
        if self.sample_size <= 0:
            return 0.0
        return self.anomaly_count / self.sample_size

    def to_dict(self) -> dict[str, Any]:
        """脱敏聚合形态（只回计数与比率，不含任何明细）。"""
        return {
            "sample_size": self.sample_size,
            "anomaly_count": self.anomaly_count,
            "anomaly_rate": self.anomaly_rate,
        }


def validate_canary_percent(percent: int) -> int:
    """校验小流量百分比落在 1%–10%（越界即拒绝，不夹取——夹取会静默改变发布范围）。"""
    if not CANARY_PERCENT_MIN <= percent <= CANARY_PERCENT_MAX:
        raise SafetyCanaryOutOfRange(
            f"小流量百分比必须落在 {CANARY_PERCENT_MIN}%–{CANARY_PERCENT_MAX}%（FR-52），"
            f"当前为 {percent}%"
        )
    return percent


def ensure_stage_transition(current: str, target: str) -> None:
    """校验阶段转移是否被允许（不可跳跃）。"""
    allowed = ALLOWED_STAGE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise SafetyStateConflict(f"发布阶段不允许 {current} → {target}（不可跳跃，FR-52）")


async def get_release_by_proposal(
    session: AsyncSession, proposal_id: uuid.UUID
) -> SafetyRelease | None:
    """按提案取发布记录（一对一）。"""
    result = await session.execute(
        select(SafetyRelease).where(SafetyRelease.proposal_id == proposal_id)
    )
    return result.scalars().first()


async def get_release(session: AsyncSession, release_id: uuid.UUID) -> SafetyRelease:
    """按 ID 取发布记录，不存在即 404。"""
    row = await session.get(SafetyRelease, release_id)
    if row is None:
        raise SafetyProposalNotFound("受控发布记录不存在")
    return row


async def start_shadow(
    session: AsyncSession,
    *,
    proposal_id: uuid.UUID,
    manifest_sha256: str,
    signer: ReleaseSigner,
) -> SafetyRelease:
    """启动影子验证（提案须为 APPROVED；清单签名）。

    Raises:
        SafetyStateConflict: 提案非 APPROVED（不可跳跃）。
        SafetySignatureInvalid: 私钥缺失或清单非法。
    """
    proposal = await proposal_service.get_proposal(session, proposal_id)
    if proposal.status != proposal_service.STATUS_APPROVED:
        raise SafetyStateConflict(
            f"仅 APPROVED 提案可启动发布（不可跳跃，FR-52），当前为 {proposal.status}"
        )
    existing = await get_release_by_proposal(session, proposal_id)
    if existing is not None:
        raise SafetyStateConflict("该提案已有发布记录（safety_releases 一对一）")

    manifest = ReleaseManifest(
        proposal_id=str(proposal_id),
        manifest_sha256=manifest_sha256,
        stage=STAGE_SHADOW_RUNNING,
        canary_percent=None,
    )
    signature = signer.sign(manifest)
    row = SafetyRelease(
        proposal_id=proposal_id,
        stage=STAGE_SHADOW_RUNNING,
        canary_percent=None,
        manifest_sha256=manifest_sha256,
        signature=signature,
        signature_key_id=signer.key_id,
    )
    session.add(row)
    await session.flush()
    return row


async def promote_to_canary(
    session: AsyncSession,
    *,
    release_id: uuid.UUID,
    canary_percent: int,
    manifest_sha256: str,
    signer: ReleaseSigner,
    observation: ObservationSample | None = None,
) -> SafetyRelease:
    """影子验证通过 → 小流量（1%–10%；清单重新签名）。"""
    percent = validate_canary_percent(canary_percent)
    if observation is not None:
        observation.validate()
    row = await get_release(session, release_id)
    ensure_stage_transition(row.stage, STAGE_CANARY)
    if row.manifest_sha256 != manifest_sha256:
        raise SafetySignatureInvalid("发布清单摘要与影子阶段不一致（清单被换）")

    manifest = ReleaseManifest(
        proposal_id=str(row.proposal_id),
        manifest_sha256=manifest_sha256,
        stage=STAGE_CANARY,
        canary_percent=percent,
    )
    row.stage = STAGE_CANARY
    row.canary_percent = percent
    row.signature = signer.sign(manifest)
    row.signature_key_id = signer.key_id
    session.add(row)
    await session.flush()
    return row


async def promote_to_production(
    session: AsyncSession,
    *,
    release_id: uuid.UUID,
    manifest_sha256: str,
    signer: ReleaseSigner,
    observation: ObservationSample,
    anomaly_threshold: float = 0.05,
) -> SafetyRelease:
    """小流量通过 → 晋升授权（观察样本超阈值即拒绝晋升）。

    Args:
        anomaly_threshold: 允许的异常率上限（默认 5%）。超阈值**不自动晋升**——
            晋升是授权动作，异常必须由人来裁决回滚还是继续（FR-52）。
    """
    observation.validate()
    row = await get_release(session, release_id)
    ensure_stage_transition(row.stage, STAGE_PROMOTION_APPROVED)
    if row.manifest_sha256 != manifest_sha256:
        raise SafetySignatureInvalid("发布清单摘要与小流量阶段不一致（清单被换）")
    if observation.anomaly_rate > anomaly_threshold:
        raise SafetyStateConflict(
            f"小流量异常率 {observation.anomaly_rate:.4f} 超过阈值 {anomaly_threshold}，"
            "拒绝晋升（须人工裁决，FR-52）"
        )
    manifest = ReleaseManifest(
        proposal_id=str(row.proposal_id),
        manifest_sha256=manifest_sha256,
        stage=STAGE_PROMOTION_APPROVED,
        canary_percent=row.canary_percent,
    )
    row.stage = STAGE_PROMOTION_APPROVED
    row.signature = signer.sign(manifest)
    row.signature_key_id = signer.key_id
    session.add(row)
    await session.flush()
    return row


async def rollback_release(
    session: AsyncSession,
    *,
    release_id: uuid.UUID,
    manifest_sha256: str,
    signer: ReleaseSigner,
    reason: str | None = None,
) -> SafetyRelease:
    """异常 → 受控回滚（`ROLLED_BACK` 为终态）。"""
    row = await get_release(session, release_id)
    if row.stage in TERMINAL_STAGES:
        raise SafetyStateConflict(f"发布已处于终态 {row.stage}，不可重复回滚")
    if row.manifest_sha256 != manifest_sha256:
        raise SafetySignatureInvalid("发布清单摘要不一致（清单被换）")
    manifest = ReleaseManifest(
        proposal_id=str(row.proposal_id),
        manifest_sha256=manifest_sha256,
        stage=STAGE_ROLLED_BACK,
        canary_percent=row.canary_percent,
    )
    row.stage = STAGE_ROLLED_BACK
    row.signature = signer.sign(manifest)
    row.signature_key_id = signer.key_id
    session.add(row)
    await session.flush()
    return row


def verify_release_signature(
    row: SafetyRelease, public_keys: list[str]
) -> None:
    """校验发布记录当前签名的有效性（查询 / 晋升前置）。

    Raises:
        SafetySignatureInvalid: 签名缺失或不匹配。
    """
    manifest = ReleaseManifest(
        proposal_id=str(row.proposal_id),
        manifest_sha256=row.manifest_sha256,
        stage=row.stage,
        canary_percent=row.canary_percent,
    )
    require_valid_release(manifest, row.signature, public_keys)


__all__ = [
    "ALLOWED_STAGE_TRANSITIONS",
    "CANARY_PERCENT_MAX",
    "CANARY_PERCENT_MIN",
    "STAGE_CANARY",
    "STAGE_PROMOTION_APPROVED",
    "STAGE_ROLLED_BACK",
    "STAGE_SHADOW_RUNNING",
    "TERMINAL_STAGES",
    "ObservationSample",
    "ensure_stage_transition",
    "get_release",
    "get_release_by_proposal",
    "promote_to_canary",
    "promote_to_production",
    "rollback_release",
    "start_shadow",
    "validate_canary_percent",
    "verify_release_signature",
]
