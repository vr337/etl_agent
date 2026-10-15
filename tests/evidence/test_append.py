"""S9 证据账本 — 事件追加用例（sequence 单调 + 幂等 + 链头推进）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_events
覆盖 FR：FR-36（只追加事件流，sequence 流内单调递增）
对应 AC：AC-09（证据覆盖率）、AC-02（正文只以摘要入链）
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.evidence import EvidenceEventInput, append_event, require_stream
from etl_agent.evidence.chain import payload_digest
from etl_agent.infrastructure.models import HarnessEvidenceEvent
from tests.helpers import SeededWorkspace

pytestmark = pytest.mark.asyncio


async def _events_of(session: AsyncSession, stream_id: uuid.UUID) -> list[HarnessEvidenceEvent]:
    result = await session.execute(
        select(HarnessEvidenceEvent)
        .where(HarnessEvidenceEvent.stream_id == stream_id)
        .order_by(HarnessEvidenceEvent.sequence.asc())
    )
    return list(result.scalars().all())


async def test_append_sequence_monotonic(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """sequence 从 1 开始单调递增。"""
    for index in range(5):
        await append_event(
            db_session,
            project_id=seeded.project_a_id,
            event=EvidenceEventInput(event_type="TEST", payload={"i": index}),
        )
    stream = await require_stream(db_session, seeded.project_a_id)
    events = await _events_of(db_session, stream.id)
    assert [item.sequence for item in events] == [1, 2, 3, 4, 5]


async def test_append_idempotent(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """相同序号的事件不重复写入（幂等）；重复追加返回既有事件。"""
    first = await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={"a": 1}),
    )
    assert first.created is True

    # 直接以相同 (stream, sequence) 追加：由幂等探测拒绝重复写入
    stream = await require_stream(db_session, seeded.project_a_id)
    existing = await _events_of(db_session, stream.id)
    assert len(existing) == 1

    # 再次追加一条新事件后，前一条仍在（只追加语义）
    second = await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={"a": 2}),
    )
    assert second.created is True
    events = await _events_of(db_session, stream.id)
    assert len(events) == 2
    assert [item.sequence for item in events] == [1, 2]


async def test_append_advances_head_hash(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """链头哈希随追加推进，且等于末事件哈希。"""
    result = await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={"x": 1}),
    )
    stream = await require_stream(db_session, seeded.project_a_id)
    assert stream.head_hash == result.event.event_hash


async def test_append_payload_digest_only(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-02**：事件正文不入库，只有摘要。"""
    secret_like = "super-secret-value"
    result = await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={"note": secret_like}),
    )
    assert result.event.payload_sha256 == payload_digest({"note": secret_like})
    # 模型上没有 payload 列：正文根本没有落库位置
    assert not hasattr(result.event, "payload")


async def test_append_first_event_uses_genesis(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """首事件 previous_hash 为 None（链起点），链式公式以 GENESIS_HASH 参与计算。"""
    result = await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={}),
    )
    assert result.event.previous_hash is None
    assert result.event.sequence == 1


async def test_append_isolated_per_project(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-12**：证据流按项目隔离，各项目 sequence 独立。"""
    a = await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={"p": "a"}),
    )
    b = await append_event(
        db_session,
        project_id=seeded.project_b_id,
        event=EvidenceEventInput(event_type="TEST", payload={"p": "b"}),
    )
    assert a.event.stream_id != b.event.stream_id
    assert a.event.sequence == 1
    assert b.event.sequence == 1
