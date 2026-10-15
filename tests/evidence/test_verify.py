"""S9 证据账本 — 整流验证报告用例（DoD-14）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N（整流验证报告）
覆盖 FR：FR-36（valid / event_count / head_hash / errors）
对应 AC：AC-09（证据可验证）、AC-02（报告不含 payload 明文）
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.evidence import EvidenceEventInput, append_event, require_stream
from etl_agent.evidence.verify import verify_stream
from etl_agent.infrastructure.models import HarnessEvidenceEvent
from tests.helpers import SeededWorkspace


async def _seed(session: AsyncSession, project_id, count: int) -> None:
    for index in range(count):
        await append_event(
            session,
            project_id=project_id,
            event=EvidenceEventInput(event_type="TEST", payload={"i": index}),
        )


async def test_verify_valid_stream(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """完整流 → valid=True + event_count + head_hash。"""
    await _seed(db_session, seeded.project_a_id, 5)
    report = await verify_stream(db_session, project_id=seeded.project_a_id)
    assert report.valid is True
    assert report.event_count == 5
    assert report.head_hash is not None and len(report.head_hash) == 64
    assert report.errors == []


async def test_verify_empty_stream(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """空流：valid=True + event_count=0 + head_hash=None（链未开始，不算错误）。"""
    # 先建流但不追加事件
    from etl_agent.evidence.stream import get_or_create_stream

    await get_or_create_stream(db_session, seeded.project_a_id)
    report = await verify_stream(db_session, project_id=seeded.project_a_id)
    assert report.valid is True
    assert report.event_count == 0
    assert report.head_hash is None


async def test_verify_invalid_stream(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """断链 → valid=False + errors[]（DoD-14）。"""
    await _seed(db_session, seeded.project_a_id, 4)
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
    # 篡改第 2 条的 previous_hash（模拟链被截断后重接）
    rows[1].previous_hash = "9" * 64
    db_session.add(rows[1])
    await db_session.flush()

    report = await verify_stream(db_session, project_id=seeded.project_a_id)
    assert report.valid is False
    assert report.errors, "断链必须给出 errors[] 定位"
    assert any(item["reason"] == "previous_hash_mismatch" for item in report.errors)


async def test_verify_report_shape(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """报告形态与 SPEC 3.2 M-N 一致（valid / event_count / head_hash / errors）。"""
    await _seed(db_session, seeded.project_a_id, 2)
    report = await verify_stream(db_session, project_id=seeded.project_a_id)
    payload = report.to_dict()
    assert set(payload) == {"valid", "event_count", "head_hash", "errors"}
    assert isinstance(payload["valid"], bool)
    assert isinstance(payload["event_count"], int)
    assert isinstance(payload["errors"], list)


async def test_verify_zero_plaintext_in_report(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-02**：验证报告不含 payload 明文（只含摘要与序号）。"""
    marker = "report-plaintext-marker"
    await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={"v": marker}),
    )
    report = await verify_stream(db_session, project_id=seeded.project_a_id)
    assert marker not in str(report.to_dict())


async def test_verify_detects_multiple_errors(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """多处断点一次性全报（不只给第一个，便于审计人员定位）。"""
    await _seed(db_session, seeded.project_a_id, 5)
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
    rows[1].payload_sha256 = "1" * 64
    rows[3].payload_sha256 = "2" * 64
    db_session.add_all([rows[1], rows[3]])
    await db_session.flush()

    report = await verify_stream(db_session, project_id=seeded.project_a_id)
    assert report.valid is False
    mismatched = [
        item for item in report.errors if item["reason"] == "event_hash_mismatch"
    ]
    assert len(mismatched) >= 2
