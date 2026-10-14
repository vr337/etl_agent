"""M-C 凭据治理 — 告警 Webhook 投递（T-06.6 / FR-10）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C
覆盖 FR：FR-10（Webhook 外置投递，退避冷却：预警 24h / 逾期 6h）
对应 AC：AC-02（投递载荷只含聚合事实，绝不含凭据）

退避冷却语义：一次成功投递后，冷却窗口内不重复投递（防告警风暴）；
窗口长度按级别区分——WARNING 24h、OVERDUE 6h（逾期更紧急）。
sender 可注入（测试用假发送器），默认 httpx POST。
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import CredentialAlertDelivery, CredentialExpiryAlert
from etl_agent.secrets.errors import SecretNotFound
from etl_agent.secrets.expiry import OVERDUE, WARNING

# 退避冷却（FR-10）：预警 24h / 逾期 6h
COOLDOWN_SECONDS = {WARNING: 24 * 3600, OVERDUE: 6 * 3600}
DEFAULT_COOLDOWN_SECONDS = 24 * 3600

# 投递状态（S1 模型 status 取值）
DELIVERY_PENDING = "PENDING"
DELIVERY_SENDING = "SENDING"
DELIVERY_SENT = "SENT"
DELIVERY_FAILED = "FAILED"

# 投递结果（deliver 返回值）
OUTCOME_SENT = "SENT"
OUTCOME_COOLING = "COOLING"
OUTCOME_FAILED = "FAILED"

AlertSender = Callable[[str, dict[str, object]], Awaitable[None]]

WEBHOOK_TIMEOUT_SECONDS = 10


def cooldown_seconds(level: str) -> int:
    """按告警级别取冷却窗口（FR-10）。"""
    return COOLDOWN_SECONDS.get(level, DEFAULT_COOLDOWN_SECONDS)


async def _default_sender(url: str, payload: dict[str, object]) -> None:
    """默认发送器：httpx POST（超时 10s，与 OTLP 导出口径一致）。"""
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


def build_payload(alert: CredentialExpiryAlert) -> dict[str, object]:
    """投递载荷：聚合事实（级别/账龄/连接），绝不含凭据（AC-02 / PRD 10.5）。"""
    return {
        "alert_id": str(alert.id),
        "connection_id": str(alert.connection_id),
        "level": alert.level,
        "credential_age_days": alert.credential_age_days,
        "dedupe_key": alert.dedupe_key,
    }


async def create_delivery(
    session: AsyncSession, *, alert_id: uuid.UUID, webhook_url: str
) -> CredentialAlertDelivery:
    """为预警创建投递记录（FR-10）。

    Raises:
        SecretNotFound: 预警不存在。
    """
    result = await session.execute(
        select(CredentialExpiryAlert).where(CredentialExpiryAlert.id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise SecretNotFound("预警不存在")
    delivery = CredentialAlertDelivery(
        alert_id=alert_id,
        webhook_url=webhook_url,
        status=DELIVERY_PENDING,
    )
    session.add(delivery)
    await session.flush()
    return delivery


async def deliver(
    session: AsyncSession,
    delivery_id: uuid.UUID,
    *,
    sender: AlertSender | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """执行一次投递（FR-10：冷却窗口内跳过）。

    Args:
        session: 数据库会话。
        delivery_id: 投递记录。
        sender: 可注入的发送器（测试用）；默认 httpx POST。
        now: 当前时间（测试可注入）。

    Returns:
        {"outcome": SENT|COOLING|FAILED, "next_attempt_at": ...}。
    """
    current = now or datetime.now(UTC)
    result = await session.execute(
        select(CredentialAlertDelivery).where(CredentialAlertDelivery.id == delivery_id)
    )
    delivery = result.scalar_one_or_none()
    if delivery is None:
        raise SecretNotFound("投递记录不存在")
    alert_result = await session.execute(
        select(CredentialExpiryAlert).where(CredentialExpiryAlert.id == delivery.alert_id)
    )
    alert = alert_result.scalar_one()

    cooldown = cooldown_seconds(alert.level)
    if delivery.next_attempt_at is not None and current < delivery.next_attempt_at:
        # 冷却窗口内：跳过本次投递（防告警风暴，FR-10）
        return {"outcome": OUTCOME_COOLING, "next_attempt_at": delivery.next_attempt_at}

    delivery.status = DELIVERY_SENDING
    delivery.attempt_count += 1
    await session.flush()
    try:
        await (sender or _default_sender)(delivery.webhook_url, build_payload(alert))
    except Exception as exc:  # noqa: BLE001  投递失败不阻断扫描主流程，记录脱敏错误
        delivery.status = DELIVERY_FAILED
        delivery.last_error = str(exc)[:500]
        delivery.next_attempt_at = current + timedelta(seconds=cooldown)
        await session.flush()
        return {"outcome": OUTCOME_FAILED, "next_attempt_at": delivery.next_attempt_at}
    delivery.status = DELIVERY_SENT
    delivery.delivered_at = current
    delivery.next_attempt_at = current + timedelta(seconds=cooldown)
    await session.flush()
    return {"outcome": OUTCOME_SENT, "next_attempt_at": delivery.next_attempt_at}
