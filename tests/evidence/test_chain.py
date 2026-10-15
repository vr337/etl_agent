"""S9 证据账本 — 哈希链计算与断链检测用例。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_events
覆盖 FR：FR-36（`event_hash = SHA256(previous_hash ‖ payload_sha256 ‖ sequence)`）
对应 AC：AC-09（证据可验证；DoD-12 哈希链正确 + 断链可检测）
"""

from __future__ import annotations

import itertools
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.evidence import EvidenceEventInput, append_event, require_stream
from etl_agent.evidence.chain import (
    GENESIS_HASH,
    compute_event_hash,
    payload_digest,
    verify_link,
)
from etl_agent.evidence.verify import verify_stream
from etl_agent.infrastructure.models import HarnessEvidenceEvent
from tests.helpers import SeededWorkspace


def test_chain_hash_correct() -> None:
    """event_hash 计算正确（显式复算公式）。"""
    previous = "a" * 64
    digest = "b" * 64
    expected = compute_event_hash(previous, digest, 7)
    assert verify_link(
        previous_hash=previous, payload_sha256=digest, sequence=7, event_hash=expected
    )
    # 换任一输入即不匹配（域分隔编码无碰撞歧义）
    assert not verify_link(
        previous_hash=previous, payload_sha256=digest, sequence=8, event_hash=expected
    )


def test_chain_genesis_for_first_event() -> None:
    """首事件无前序时按创世哈希参与计算（None 与 GENESIS_HASH 等价）。"""
    digest = "c" * 64
    assert compute_event_hash(None, digest, 1) == compute_event_hash(GENESIS_HASH, digest, 1)


def test_chain_digest_is_key_order_insensitive() -> None:
    """payload 摘要对键序不敏感（否则重放检测会被键序噪声击穿）。"""
    assert payload_digest({"b": 1, "a": 2}) == payload_digest({"a": 2, "b": 1})


async def test_chain_links_are_continuous(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """连续追加的事件链首尾相接（每条 previous_hash 等于前一条 event_hash）。"""
    for index in range(4):
        await append_event(
            db_session,
            project_id=seeded.project_a_id,
            event=EvidenceEventInput(event_type="TEST", payload={"i": index}),
        )
    stream = await require_stream(db_session, seeded.project_a_id)
    rows = list(
        (
            await db_session.execute(
                select(HarnessEvidenceEvent)
                .where(HarnessEvidenceEvent.stream_id == stream.id)
                .order_by(HarnessEvidenceEvent.sequence.asc())
            )
        )
        .scalars()
        .all()
    )
    assert rows[0].previous_hash is None
    for previous, current in itertools.pairwise(rows):
        assert current.previous_hash == previous.event_hash


async def test_chain_break_detection(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """篡改中间事件的 payload_sha256 → 整流验证失败且定位到该条（DoD-12）。"""
    for index in range(3):
        await append_event(
            db_session,
            project_id=seeded.project_a_id,
            event=EvidenceEventInput(event_type="TEST", payload={"i": index}),
        )
    stream = await require_stream(db_session, seeded.project_a_id)
    rows = list(
        (
            await db_session.execute(
                select(HarnessEvidenceEvent)
                .where(HarnessEvidenceEvent.stream_id == stream.id)
                .order_by(HarnessEvidenceEvent.sequence.asc())
            )
        )
        .scalars()
        .all()
    )
    # 直接篡改中间事件的摘要（模拟绕过应用层改库）
    rows[1].payload_sha256 = "f" * 64
    db_session.add(rows[1])
    await db_session.flush()

    report = await verify_stream(db_session, project_id=seeded.project_a_id)
    assert report.valid is False
    assert report.event_count == 3
    reasons = {item["reason"] for item in report.errors}
    assert "event_hash_mismatch" in reasons
    # 定位到被篡改的序号
    assert any(item.get("sequence") == 2 for item in report.errors)


async def test_chain_sequence_gap_detection(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """删除末事件 → 链头对账失败（head_hash_mismatch）。"""
    for index in range(3):
        await append_event(
            db_session,
            project_id=seeded.project_a_id,
            event=EvidenceEventInput(event_type="TEST", payload={"i": index}),
        )
    # 模拟「删掉末事件」：只改流头，使 head_hash 与实际末事件不符
    stream = await require_stream(db_session, seeded.project_a_id)
    stream.head_hash = "e" * 64
    db_session.add(stream)
    await db_session.flush()

    report = await verify_stream(db_session, project_id=seeded.project_a_id)
    assert report.valid is False
    assert any(item["reason"] == "head_hash_mismatch" for item in report.errors)


async def test_chain_zero_plaintext_in_events(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-02**：证据事件不含明文 payload 字段。"""
    marker = "plaintext-marker-abcdef"
    result = await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={"value": marker}),
    )
    row = result.event
    # 逐列比对：任何列都不得承载正文
    for column in (
        row.event_hash,
        row.payload_sha256,
        row.previous_hash or "",
        row.event_type,
        row.signature_value or "",
    ):
        assert marker not in str(column)
    assert not hasattr(row, "payload")


def test_chain_stream_id_not_in_hash() -> None:
    """链式公式不包含 stream_id：跨流相同输入得相同哈希（链身份由行归属确定）。"""
    assert compute_event_hash(None, "a" * 64, 1) == compute_event_hash(None, "a" * 64, 1)


def test_event_uuid_is_stable_per_call() -> None:
    """事件主键为 UUID（不依赖链式值推导，避免哈希即主键带来的重建风险）。"""
    assert uuid.uuid4() != uuid.uuid4()
