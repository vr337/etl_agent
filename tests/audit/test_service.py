"""S9 全量审计 — 审计事件记录服务用例（脱敏 + 落库 + 失败兜底）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 audit_events
覆盖 FR：FR-36（actor/project/action/resource/before/after/reason/trace_id）
对应 AC：AC-02（before/after 零明文）
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.audit.service import REDACTED, AuditEventInput, record, record_safely
from etl_agent.infrastructure.models import AuditEvent
from tests.helpers import SeededWorkspace

pytestmark = pytest.mark.asyncio


async def _rows(session: AsyncSession, project_id: uuid.UUID) -> list[AuditEvent]:
    result = await session.execute(
        select(AuditEvent).where(AuditEvent.project_id == project_id)
    )
    return list(result.scalars().all())


async def test_audit_before_after_persisted(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """before / after 落库（全字段）。"""
    await record(
        db_session,
        AuditEventInput(
            project_id=seeded.project_a_id,
            actor_id=seeded.admin_a_user_id,
            action="TEST_ACTION",
            resource_type="TEST_RESOURCE",
            resource_id="res-1",
            before={"status": "A"},
            after={"status": "B"},
            reason="测试",
            trace_id="trace-1",
        ),
    )
    rows = await _rows(db_session, seeded.project_a_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.before == {"status": "A"}
    assert row.after == {"status": "B"}
    assert row.reason == "测试"
    assert row.trace_id == "trace-1"
    assert row.actor_id == seeded.admin_a_user_id


async def test_audit_no_plaintext(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-02**：凭证明文在 before/after 中被脱敏。"""
    await record(
        db_session,
        AuditEventInput(
            project_id=seeded.project_a_id,
            actor_id=seeded.admin_a_user_id,
            action="TEST_ACTION",
            resource_type="CONNECTION",
            resource_id="conn-1",
            before={"password": "real-secret", "host": "db.local"},
            after={"token": "abc123", "api_key": "k-999", "status": "READY"},
        ),
    )
    row = (await _rows(db_session, seeded.project_a_id))[0]
    assert row.before is not None and row.after is not None
    assert row.before["password"] == REDACTED
    assert row.before["host"] == "db.local"  # 非敏感字段保留
    assert row.after["token"] == REDACTED
    assert row.after["api_key"] == REDACTED
    assert row.after["status"] == "READY"
    # 明文不出现在任何列的序列化结果里
    assert "real-secret" not in str(row.before)
    assert "abc123" not in str(row.after)


async def test_audit_nested_sanitize(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """嵌套结构中的敏感键也被脱敏（递归）。"""
    await record(
        db_session,
        AuditEventInput(
            project_id=seeded.project_a_id,
            actor_id=seeded.admin_a_user_id,
            action="TEST_ACTION",
            resource_type="CONNECTION",
            resource_id="conn-2",
            after={"nested": {"secret_value": "x", "items": [{"credential": "y"}]}},
        ),
    )
    row = (await _rows(db_session, seeded.project_a_id))[0]
    assert row.after is not None
    assert row.after["nested"]["secret_value"] == REDACTED
    assert row.after["nested"]["items"][0]["credential"] == REDACTED


async def test_audit_on_failure_does_not_raise(
    db_session: AsyncSession, seeded: SeededWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**DoD-15**：记录失败也落审计（record_safely 不抛异常，降级为日志）。"""

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("模拟审计写入故障")

    monkeypatch.setattr("etl_agent.audit.service.record", _boom)
    result = await record_safely(
        db_session,
        AuditEventInput(
            project_id=seeded.project_a_id,
            actor_id=seeded.admin_a_user_id,
            action="TEST_ACTION",
            resource_type="TEST_RESOURCE",
            resource_id="res-x",
        ),
    )
    assert result is None  # 降级而非抛出
