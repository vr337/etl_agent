"""M-C 凭据治理 — 凭据轮换状态机（T-06.1 / FR-07）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C
覆盖 FR：FR-07（VALIDATING→WAITING_APPROVAL→APPROVED→ACTIVATING→SUCCEEDED/FAILED）
对应 AC：AC-02（新凭据经 Provider 加密暂存，激活前旧凭据继续可用）

关键语义（S3 提示词 9.7）：
- 新凭据先暂存（new_secret_ref），真实连通性验证通过才进入审批；
- **激活前 data_connections.secret_ref 始终指向旧凭据**——轮换不影响在途作业；
- 激活是 begin/finish 两步：begin 落 ACTIVATING（可观测），finish 才换引用；
- 未激活即中止（rollback）→ 旧凭据原样保留，其 created_at 不变 → 原始年龄不刷新。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.connections.service import load_connection
from etl_agent.infrastructure.models import DataConnection, SecretRotationRequest
from etl_agent.infrastructure.pagination import paginate
from etl_agent.secrets.errors import SecretNotFound, SecretStateConflict
from etl_agent.secrets.factory import create_provider

# 轮换状态机（FR-07；S1 模型 status 为 String，取值由本模块定义）
VALIDATING = "VALIDATING"
WAITING_APPROVAL = "WAITING_APPROVAL"
APPROVED = "APPROVED"
ACTIVATING = "ACTIVATING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"

# 激活前状态（可回滚集合）：这些状态下连接仍持旧凭据
PRE_ACTIVATION_STATES = frozenset({VALIDATING, WAITING_APPROVAL, APPROVED})


async def _load_rotation(
    session: AsyncSession, project_id: uuid.UUID, rotation_id: uuid.UUID
) -> SecretRotationRequest:
    """按项目加载轮换申请（经 connection 做项目隔离，AC-12）。"""
    result = await session.execute(
        select(SecretRotationRequest)
        .join(DataConnection, DataConnection.id == SecretRotationRequest.connection_id)
        .where(
            SecretRotationRequest.id == rotation_id,
            DataConnection.project_id == project_id,
        )
    )
    rotation = result.scalar_one_or_none()
    if rotation is None:
        raise SecretNotFound("轮换申请不存在")
    return rotation


async def list_rotations_for_connection(
    session: AsyncSession,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SecretRotationRequest], int]:
    """按连接列出轮换申请（联调修复新增的列表读取；项目隔离经 load_connection，AC-12）。

    连接不存在或不属于路径项目时 `load_connection` 先抛 404（与该路径下其余路由一致），
    再按 `created_at` 倒序 + `id` 兜底做稳定分页；总数由调用方经 `X-Total-Count` 回传。
    """
    await load_connection(session, project_id, connection_id)
    rows, total = await paginate(
        session,
        select(SecretRotationRequest).where(SecretRotationRequest.connection_id == connection_id),
        order_by=(SecretRotationRequest.created_at.desc(), SecretRotationRequest.id.desc()),
        limit=limit,
        offset=offset,
    )
    return [row[0] for row in rows], total


async def create_rotation(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    new_username: str,
    new_password: str,
    reason: str | None,
    requested_by: uuid.UUID,
    settings: Settings,
) -> SecretRotationRequest:
    """创建轮换申请并暂存新凭据（FR-07：VALIDATING）。

    新凭据经 SecretProvider 加密后写入 new_secret_ref；连接引用不变（仍持旧凭据）。
    """
    await load_connection(session, project_id, connection_id)
    provider = create_provider(session, settings)
    staged = await provider.store({"username": new_username, "password": new_password})
    rotation = SecretRotationRequest(
        connection_id=connection_id,
        status=VALIDATING,
        new_secret_ref=staged.secret_id,
        requested_by=requested_by,
        reason=reason,
    )
    session.add(rotation)
    await session.flush()
    return rotation


async def validate_rotation(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    rotation_id: uuid.UUID,
    settings: Settings,
) -> SecretRotationRequest:
    """用暂存的新凭据做真实连通性验证（FR-07：VALIDATING → WAITING_APPROVAL / FAILED）。"""
    rotation = await _load_rotation(session, project_id, rotation_id)
    _require_status(rotation, {VALIDATING})
    provider = create_provider(session, settings)
    assert rotation.new_secret_ref is not None  # VALIDATING 状态必有暂存凭据
    staged = await provider.read(rotation.new_secret_ref)
    connection = await load_connection(session, project_id, rotation.connection_id)
    # 复用 API-03 探针路径，但凭据换成暂存的新凭据（内存中短暂持有）
    from etl_agent.connections.probes import ProbeConfig, probe_for

    config = ProbeConfig(
        connector_type=connection.connector_type,
        host=connection.host or "",
        port=connection.port,
        database_name=connection.database_name,
        username=staged.get("username", ""),
        password=staged.get("password", ""),
    )
    from etl_agent.connections.probe import mask_error
    from etl_agent.connections.probes.base import ProbeError

    try:
        await probe_for(config.connector_type)(config)
    except ProbeError as exc:
        rotation.status = FAILED
        rotation.failure_reason = mask_error(str(exc), [config.password, config.username])
        rotation.completed_at = datetime.now(UTC)
        await session.flush()
        return rotation
    rotation.status = WAITING_APPROVAL
    await session.flush()
    return rotation


async def approve_rotation(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    rotation_id: uuid.UUID,
    approved_by: uuid.UUID,
) -> SecretRotationRequest:
    """审批轮换（FR-07：WAITING_APPROVAL → APPROVED）。"""
    rotation = await _load_rotation(session, project_id, rotation_id)
    _require_status(rotation, {WAITING_APPROVAL})
    rotation.status = APPROVED
    rotation.approved_by = approved_by
    await session.flush()
    return rotation


async def begin_activation(
    session: AsyncSession, *, project_id: uuid.UUID, rotation_id: uuid.UUID
) -> SecretRotationRequest:
    """进入激活（FR-07：APPROVED → ACTIVATING；可观测的中间态）。"""
    rotation = await _load_rotation(session, project_id, rotation_id)
    _require_status(rotation, {APPROVED})
    rotation.status = ACTIVATING
    await session.flush()
    return rotation


async def finish_activation(
    session: AsyncSession, *, project_id: uuid.UUID, rotation_id: uuid.UUID
) -> SecretRotationRequest:
    """完成激活（FR-07：ACTIVATING → SUCCEEDED）：切换连接引用到新凭据。

    此刻旧凭据才失效（成为可销毁的轮换源，FR-09）；切换后 created_at 不动的旧 Secret
    其「年龄」以自身 created_at 计——轮换不刷新旧凭据年龄（FR-07 回滚语义同源）。
    """
    rotation = await _load_rotation(session, project_id, rotation_id)
    _require_status(rotation, {ACTIVATING})
    connection = await load_connection(session, project_id, rotation.connection_id)
    assert rotation.new_secret_ref is not None
    connection.secret_ref = rotation.new_secret_ref
    rotation.status = SUCCEEDED
    rotation.completed_at = datetime.now(UTC)
    await session.flush()
    return rotation


def _require_status(rotation: SecretRotationRequest, allowed: frozenset[str] | set[str]) -> None:
    """状态守卫（FR-07：状态机不可跳跃）。"""
    if rotation.status not in allowed:
        raise SecretStateConflict(
            f"轮换状态 {rotation.status} 不允许该操作，允许: {sorted(allowed)}"
        )
