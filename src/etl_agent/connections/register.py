"""M-C 数据连接 — 连接登记（API-02，Secret 引用制）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-02 / 第 3.2 章 M-C
覆盖 FR：FR-04（异构连接登记、项目内唯一名、Secret 引用制不存明文）、FR-06（Provider 加密）
对应 AC：AC-02（响应只含 secret_ref 与定位信息，绝无凭据明文）
"""
from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.connections.errors import ConnectionNameDuplicate
from etl_agent.connections.schemas import (
    DEFAULT_MAX_AGE_DAYS,
    DEFAULT_WARNING_DAYS,
    ConnectionRegisterRequest,
)
from etl_agent.connections.service import name_exists, validate_connector_type
from etl_agent.infrastructure.models import DataConnection
from etl_agent.secrets.factory import create_provider


async def register_connection(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    payload: ConnectionRegisterRequest,
    settings: Settings,
) -> DataConnection:
    """登记连接（API-02 / FR-04）。

    流程（S3 提示词 9.4）：
    1. 校验连接器类型与 (project_id, name) 唯一（应用层预检 + DB UK 兜底）；
    2. username/password 经 SecretProvider 加密，得到 secret_ref；
    3. 写 data_connections（status=UNTESTED，默认轮换策略 90/14）；
    4. 凭据明文不落任何表——只在内存中短暂持有（禁止事项 16/17）。

    Raises:
        ValidationError:          连接器类型非法。
        ConnectionNameDuplicate:  项目内连接名重复。
    """
    validate_connector_type(payload.connector_type)
    if await name_exists(session, project_id, payload.name):
        raise ConnectionNameDuplicate(f"项目内连接名已存在: {payload.name}")

    # username 与 password 一并进入加密载荷（S1 模型无 username 列，禁止事项 17：不存凭据列）
    provider = create_provider(session, settings)
    secret_ref = await provider.store(
        {"username": payload.username, "password": payload.password}
    )

    connection = DataConnection(
        project_id=project_id,
        name=payload.name,
        connector_type=payload.connector_type,
        status="UNTESTED",
        host=payload.host,
        port=payload.port,
        database_name=payload.database_name,
        secret_ref=secret_ref.secret_id,
        credential_max_age_days=DEFAULT_MAX_AGE_DAYS,
        credential_warning_days=DEFAULT_WARNING_DAYS,
    )
    session.add(connection)
    try:
        await session.flush()
    except IntegrityError as exc:
        # (project_id, name) UK：并发双写场景的应用层兜底（FR-04）
        raise ConnectionNameDuplicate(f"项目内连接名已存在: {payload.name}") from exc
    return connection
