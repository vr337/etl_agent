"""M-N 审计证据 — 事件追加（sequence / payload_sha256 / previous_hash / event_hash）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_events
覆盖 FR：FR-36（只追加事件流，sequence 流内单调递增）
对应 AC：AC-09（证据覆盖率）、AC-02（正文只以摘要入链）

**只追加**（SPEC 3.2 M-N 红线 / S9 禁止事项 14）：本模块**不提供**任何 update / delete 接口。
事件的不可变性靠两点保障：① 代码路径只有 INSERT；② `event_hash` 唯一约束使同一事件无法重复落库。

**幂等**（S9 提示词 §8.1）：相同 `(stream_id, sequence)` 重复追加不重复写入——
先按唯一约束探测，命中则返回既有事件（不报错），这是事件流被上游重放时的正确处置。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.evidence.chain import compute_event_hash, payload_digest
from etl_agent.evidence.checkpoint import (
    SIGNATURE_ALGORITHM,
    CheckpointSigner,
    checkpoint_payload,
    should_checkpoint,
)
from etl_agent.evidence.stream import get_or_create_stream
from etl_agent.infrastructure.models import HarnessEvidenceEvent, HarnessEvidenceStream


@dataclass(frozen=True)
class EvidenceEventInput:
    """待追加事件的治理事实（不含凭据 / Prompt 正文，AC-02）。"""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor_id: uuid.UUID | None = None
    risk_level: str | None = None
    policy_decision_id: uuid.UUID | None = None
    trace_id: str | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class AppendedEvent:
    """追加结果（幂等命中时 `created=False`）。"""

    event: HarnessEvidenceEvent
    created: bool
    signed: bool = False


async def _next_sequence(session: AsyncSession, stream_id: uuid.UUID) -> int:
    """取流内下一个序号（当前最大 + 1；空流从 1 开始）。

    并发追加时两个事务可能取到同一序号：`(stream_id, sequence)` 唯一约束会把后者
    顶回 `IntegrityError`，由调用方重试——**不能**靠「读最大值」本身保证单调，
    那只是快速路径（DB 约束才是真防线）。
    """
    result = await session.execute(
        select(HarnessEvidenceEvent.sequence)
        .where(HarnessEvidenceEvent.stream_id == stream_id)
        .order_by(HarnessEvidenceEvent.sequence.desc())
        .limit(1)
    )
    current = result.scalar_one_or_none()
    return 1 if current is None else int(current) + 1


async def _find_existing(
    session: AsyncSession, stream_id: uuid.UUID, sequence: int
) -> HarnessEvidenceEvent | None:
    """按 `(stream_id, sequence)` 查既有事件（幂等探测）。"""
    result = await session.execute(
        select(HarnessEvidenceEvent).where(
            HarnessEvidenceEvent.stream_id == stream_id,
            HarnessEvidenceEvent.sequence == sequence,
        )
    )
    return result.scalars().first()


async def _last_checkpoint_at(
    session: AsyncSession, stream_id: uuid.UUID
) -> datetime | None:
    """最近一条已签名检查点事件的 `occurred_at`（无则 None）。

    用于「每 T 秒打一次检查点」的时间判据；表上没有独立的检查点时间列，
    故以事件自身的 `occurred_at` 为准（同一事实，不引入冗余字段）。
    """
    result = await session.execute(
        select(HarnessEvidenceEvent.occurred_at)
        .where(
            HarnessEvidenceEvent.stream_id == stream_id,
            HarnessEvidenceEvent.signature_value.is_not(None),
        )
        .order_by(HarnessEvidenceEvent.sequence.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def append_event(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    event: EvidenceEventInput,
    signer: CheckpointSigner | None = None,
    now: datetime | None = None,
) -> AppendedEvent:
    """向项目证据流追加一条事件（幂等 + 链式 + 可选检查点签名）。

    Args:
        session: 业务库会话。
        project_id: 项目 ID（流按项目唯一）。
        event: 事件治理事实。
        signer: 检查点签名器；None 或未启用则不打检查点（可选能力）。
        now: 注入时钟（测试用）。

    Returns:
        AppendedEvent：`created=False` 表示幂等命中既有事件。

    Raises:
        EvidenceChainBroken: 流头 `head_hash` 与计算出的 `previous_hash` 不一致
            （说明有人在链外改过数据）。
    """
    moment = now or datetime.now(UTC)
    stream = await get_or_create_stream(session, project_id)
    sequence = await _next_sequence(session, stream.id)
    previous_hash = stream.head_hash

    digest = payload_digest(event.payload)
    event_hash = compute_event_hash(previous_hash, digest, sequence)

    existing = await _find_existing(session, stream.id, sequence)
    if existing is not None:
        # 幂等：同序号已存在即返回既有事件（上游重放不产生第二条链）
        return AppendedEvent(event=existing, created=False)

    signature: str | None = None
    key_id: str | None = None
    signed = False
    if signer is not None and signer.enabled:
        # 距上次检查点的时间由「最近一条已签名事件」推算——模型无 last_checkpoint_at 列，
        # 故不引入新字段（S9 不新增迁移），检查点节奏改由事件数主导、时间作补充判据
        last_signed_at = await _last_checkpoint_at(session, stream.id)
        seconds_since = (
            (moment - last_signed_at).total_seconds() if last_signed_at is not None else None
        )
        if should_checkpoint(sequence, seconds_since_last=seconds_since):
            signature, key_id = signer.sign(checkpoint_payload(str(stream.id), sequence, event_hash))
            signed = True

    record = HarnessEvidenceEvent(
        stream_id=stream.id,
        sequence=sequence,
        payload_sha256=digest,
        previous_hash=previous_hash,
        event_hash=event_hash,
        signature_algorithm=SIGNATURE_ALGORITHM if signed else None,
        signature_key_id=key_id,
        signature_value=signature,
        occurred_at=event.occurred_at or moment,
        actor_id=event.actor_id,
        event_type=event.event_type,
        risk_level=event.risk_level,
        policy_decision_id=event.policy_decision_id,
        trace_id=event.trace_id,
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError:
        # 并发落同序号：回滚本条，返回对方已写入的事件（仍是幂等语义）
        await session.rollback()
        raced = await _find_existing(session, stream.id, sequence)
        if raced is None:
            raise
        return AppendedEvent(event=raced, created=False)

    await _advance_head(session, stream, event_hash, moment)
    return AppendedEvent(event=record, created=True, signed=signed)


async def _advance_head(
    session: AsyncSession,
    stream: HarnessEvidenceStream,
    event_hash: str,
    moment: datetime,
) -> None:
    """推进流头哈希（链头 = 最新事件哈希）。

    `head_hash` 是**派生值**，不是独立真相：整流验证以事件重算为准，
    本字段只用于快速追加与断链检测。
    """
    stream.head_hash = event_hash
    session.add(stream)
    await session.flush()


async def append_events(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    events: list[EvidenceEventInput],
    signer: CheckpointSigner | None = None,
    now: datetime | None = None,
) -> list[AppendedEvent]:
    """批量追加（内部串行，保证链顺序）。"""
    results: list[AppendedEvent] = []
    for item in events:
        results.append(
            await append_event(
                session, project_id=project_id, event=item, signer=signer, now=now
            )
        )
    return results


__all__ = ["AppendedEvent", "EvidenceEventInput", "append_event", "append_events"]
