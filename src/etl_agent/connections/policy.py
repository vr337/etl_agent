"""M-C 数据连接 — 轮换策略配置（T-06.7 / FR-11）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C
覆盖 FR：FR-11（credential_max_age_days 1–3650、0 < credential_warning_days < max_age）
对应 AC：AC-02（策略为数值配置，不涉凭据）
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.connections.errors import InvalidRotationPolicy
from etl_agent.connections.schemas import (
    MAX_AGE_MAX_DAYS,
    MAX_AGE_MIN_DAYS,
    ConnectionPolicyUpdateRequest,
)
from etl_agent.connections.service import load_connection
from etl_agent.infrastructure.models import DataConnection


def validate_policy(max_age_days: int, warning_days: int) -> None:
    """校验轮换策略边界（FR-11）。

    Raises:
        InvalidRotationPolicy: max_age 越界 1–3650，或 warning 不在 (0, max_age) 内。
    """
    if not (MAX_AGE_MIN_DAYS <= max_age_days <= MAX_AGE_MAX_DAYS):
        raise InvalidRotationPolicy(
            f"credential_max_age_days 须在 [{MAX_AGE_MIN_DAYS}, {MAX_AGE_MAX_DAYS}]，当前 {max_age_days}"
        )
    if not (0 < warning_days < max_age_days):
        raise InvalidRotationPolicy(
            f"credential_warning_days 须在 (0, {max_age_days}) 内，当前 {warning_days}"
        )


async def update_policy(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: ConnectionPolicyUpdateRequest,
) -> DataConnection:
    """更新连接的轮换策略（PATCH，FR-11）。

    Raises:
        ConnectionNotFound:     连接不存在（跨项目视同不存在）。
        InvalidRotationPolicy:  策略越界。
    """
    validate_policy(payload.credential_max_age_days, payload.credential_warning_days)
    connection = await load_connection(session, project_id, connection_id)
    connection.credential_max_age_days = payload.credential_max_age_days
    connection.credential_warning_days = payload.credential_warning_days
    await session.flush()
    return connection
