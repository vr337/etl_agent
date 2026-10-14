"""告警 Webhook 投递测试（T-06.6 / FR-10，S3 提示词 10）。

覆盖：投递载荷不含凭据（AC-02）；成功投递 → SENT；冷却窗口内重复 → COOLING；
发送失败 → FAILED（不阻断扫描主流程）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import CredentialAlertDelivery, CredentialExpiryAlert
from etl_agent.secrets import alert_delivery as delivery_service
from etl_agent.secrets.alert_delivery import (
    OUTCOME_COOLING,
    OUTCOME_FAILED,
    OUTCOME_SENT,
    OVERDUE,
    WARNING,
)
from tests.helpers import SeededWorkspace


async def _make_alert(
    session: AsyncSession, connection_id: uuid.UUID, level: str
) -> CredentialExpiryAlert:
    """直接构造一条预警（connection_id 必须指向真实连接以满足 FK）。"""
    alert = CredentialExpiryAlert(
        connection_id=connection_id,
        level=level,
        dedupe_key=f"cred-expiry:{connection_id}:{level}",
        credential_age_days=95 if level == OVERDUE else 80,
    )
    session.add(alert)
    await session.flush()
    return alert


def test_payload_has_no_credential() -> None:
    """投递载荷只含聚合事实；绝不含 password / ciphertext（AC-02）。"""
    alert = CredentialExpiryAlert(
        connection_id=uuid.uuid4(),
        level=OVERDUE,
        dedupe_key="cred-expiry:x:OVERDUE",
        credential_age_days=200,
    )
    payload = delivery_service.build_payload(alert)
    assert "password" not in payload
    assert "ciphertext" not in payload
    assert payload["level"] == OVERDUE
    assert str(alert.connection_id) in str(payload["connection_id"])


async def test_deliver_success(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """注入假发送器：成功投递 → SENT，且发送器收到聚合载荷。"""
    connection, _ = seeded_connection
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_sender(url: str, payload: dict[str, object]) -> None:
        calls.append((url, payload))

    alert = await _make_alert(db_session, connection.id, OVERDUE)
    delivery = await delivery_service.create_delivery(
        db_session, alert_id=alert.id, webhook_url="https://hook.test/x"
    )
    result = await delivery_service.deliver(db_session, delivery.id, sender=fake_sender)
    assert result["outcome"] == OUTCOME_SENT
    assert len(calls) == 1
    assert calls[0][0] == "https://hook.test/x"
    assert "password" not in calls[0][1]


async def test_deliver_cooling_window(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """首次成功投递后，冷却窗口内重复调用 → COOLING（防告警风暴，FR-10）。"""
    connection, _ = seeded_connection

    async def fake_sender(url: str, payload: dict[str, object]) -> None:
        return None

    alert = await _make_alert(db_session, connection.id, WARNING)
    delivery = await delivery_service.create_delivery(
        db_session, alert_id=alert.id, webhook_url="https://hook.test/x"
    )
    first = await delivery_service.deliver(db_session, delivery.id, sender=fake_sender)
    assert first["outcome"] == OUTCOME_SENT
    second = await delivery_service.deliver(db_session, delivery.id, sender=fake_sender)
    assert second["outcome"] == OUTCOME_COOLING


async def test_deliver_failure_recorded(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """发送器抛异常 → FAILED，且 next_attempt_at 被设定（不阻断上游）。"""
    connection, _ = seeded_connection

    async def boom(url: str, payload: dict[str, object]) -> None:
        raise RuntimeError("webhook 503")

    alert = await _make_alert(db_session, connection.id, OVERDUE)
    delivery = await delivery_service.create_delivery(
        db_session, alert_id=alert.id, webhook_url="https://hook.test/x"
    )
    result = await delivery_service.deliver(db_session, delivery.id, sender=boom)
    assert result["outcome"] == OUTCOME_FAILED

    row = (
        await db_session.execute(
            select(CredentialAlertDelivery).where(
                CredentialAlertDelivery.id == delivery.id
            )
        )
    ).scalar_one()
    assert row.status == "FAILED"
    assert row.last_error is not None
    assert row.next_attempt_at is not None


def test_cooldown_per_level() -> None:
    """冷却窗口按级别区分：OVERDUE 6h，WARNING 24h（FR-10）。"""
    assert delivery_service.cooldown_seconds(OVERDUE) == 6 * 3600
    assert delivery_service.cooldown_seconds(WARNING) == 24 * 3600
