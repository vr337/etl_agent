"""M-C 凭据治理 — 到期预警扫描（T-06.5 / FR-10）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C
覆盖 FR：FR-10（默认最大账龄 90 天、预警 14 天；WARNING/OVERDUE 两级 + 去重键）
对应 AC：AC-02（账龄取自 Secret 行 created_at，无需解密明文）

两级口径（按连接的 credential_max_age_days / credential_warning_days，FR-11）：
- OVERDUE：账龄已超过 max_age；
- WARNING：剩余天数 ≤ warning_days（未过期）。
去重键 dedupe_key = f"cred-expiry:{connection_id}:{level}"（S1 模型 UK）——
同连接同级别只生成一条，确认/静默/解决字段留给后续交互端点（S4 吸收清单）。

S3 不实现 Celery Beat（禁止事项 14）：本模块提供 CLI etl-agent-scan-credentials 手动触发，
Beat 周期扫描在 S7 接入。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.database import async_session_factory
from etl_agent.infrastructure.models import (
    CredentialExpiryAlert,
    DataConnection,
    EncryptedSecret,
)

# 告警级别（FR-10）
WARNING = "WARNING"
OVERDUE = "OVERDUE"

DEDUPE_KEY_PREFIX = "cred-expiry"


def dedupe_key(connection_id: uuid.UUID, level: str) -> str:
    """去重键（同连接同级别唯一，S1 模型 UK）。"""
    return f"{DEDUPE_KEY_PREFIX}:{connection_id}:{level}"


def classify(age_days: int, max_age_days: int, warning_days: int) -> str | None:
    """账龄分级（FR-10）。

    Args:
        age_days: 凭据账龄（天）。
        max_age_days: 最大账龄。
        warning_days: 预警窗口。

    Returns:
        OVERDUE / WARNING / None（健康）。
    """
    remaining = max_age_days - age_days
    if remaining < 0:
        return OVERDUE
    if remaining <= warning_days:
        return WARNING
    return None


async def scan_credentials(
    session: AsyncSession, *, now: datetime | None = None
) -> list[CredentialExpiryAlert]:
    """扫描全部连接的凭据账龄并生成预警（FR-10；幂等，去重键命中即跳过）。

    Returns:
        本次新建的预警列表（已存在的去重键不重复生成）。
    """
    current = now or datetime.now(UTC)
    result = await session.execute(
        select(DataConnection, EncryptedSecret.created_at)
        .join(EncryptedSecret, EncryptedSecret.id == DataConnection.secret_ref)
        .where(DataConnection.secret_ref.is_not(None))
    )
    created: list[CredentialExpiryAlert] = []
    for connection, secret_created_at in result.all():
        if secret_created_at is None:
            continue
        age_days = (current - secret_created_at).days
        level = classify(
            age_days, connection.credential_max_age_days, connection.credential_warning_days
        )
        if level is None:
            continue
        key = dedupe_key(connection.id, level)
        existing = await session.execute(
            select(CredentialExpiryAlert.id).where(CredentialExpiryAlert.dedupe_key == key)
        )
        if existing.scalar_one_or_none() is not None:
            continue  # 去重：同连接同级别只生成一条（FR-10）
        alert = CredentialExpiryAlert(
            connection_id=connection.id,
            level=level,
            dedupe_key=key,
            credential_age_days=age_days,
        )
        session.add(alert)
        created.append(alert)
    await session.flush()
    return created


async def _scan_once() -> int:
    """CLI 单次扫描：返回新建预警数。"""
    async with async_session_factory() as session:
        alerts = await scan_credentials(session)
        await session.commit()
        return len(alerts)


def main() -> None:
    """CLI 入口（pyproject scripts: etl-agent-scan-credentials，FR-10）。"""
    count = asyncio.run(_scan_once())
    print(f"credential expiry scan done: {count} new alert(s)")


if __name__ == "__main__":
    main()
