"""M-C 数据连接 — 连接域共享服务（加载 / 校验 / 凭据组装）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / 第 8 章「安全 · 多租户」行
覆盖 FR：FR-04（连接管理）、FR-06（Secret 引用制）
对应 AC：AC-02（响应零明文）、AC-12（跨项目视同不存在）
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.connections.errors import ConnectionNotFound
from etl_agent.connections.schemas import CONNECTOR_TYPES
from etl_agent.infrastructure.models import DataConnection


def validate_connector_type(connector_type: str) -> str:
    """校验连接器类型（8 类，FR-04）。

    Raises:
        ValidationError: 类型不在 8 类之内。
    """
    from etl_agent.auth.errors import ValidationError

    if connector_type not in CONNECTOR_TYPES:
        raise ValidationError(
            f"非法连接器类型: {connector_type!r}，可选 {sorted(CONNECTOR_TYPES)}"
        )
    return connector_type


async def load_connection(
    session: AsyncSession, project_id: uuid.UUID, connection_id: uuid.UUID
) -> DataConnection:
    """按项目加载连接（跨项目视同不存在：统一 404，AC-12）。

    Raises:
        ConnectionNotFound: 连接不存在或不属于该项目。
    """
    result = await session.execute(
        select(DataConnection).where(
            DataConnection.id == connection_id,
            DataConnection.project_id == project_id,
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise ConnectionNotFound("连接不存在")
    return connection


async def load_project_connections(
    session: AsyncSession, project_id: uuid.UUID
) -> list[DataConnection]:
    """列出项目全部连接（FR-04）。"""
    result = await session.execute(
        select(DataConnection)
        .where(DataConnection.project_id == project_id)
        .order_by(DataConnection.created_at)
    )
    return list(result.scalars().all())


async def name_exists(
    session: AsyncSession, project_id: uuid.UUID, name: str
) -> bool:
    """应用层预检 (project_id, name) 唯一（DB 层 UK 兜底，FR-04）。"""
    result = await session.execute(
        select(DataConnection.id).where(
            DataConnection.project_id == project_id, DataConnection.name == name
        )
    )
    return result.scalar_one_or_none() is not None
