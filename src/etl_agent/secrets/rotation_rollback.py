"""M-C 凭据治理 — 轮换回滚（T-06.4 / FR-07）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C
覆盖 FR：FR-07（激活失败/未激活即中止 → 回滚到旧凭据，保留原始年龄）
对应 AC：AC-02（回滚只改引用与状态，不触碰密钥材料）

「保留原始年龄」的实现要点：旧 Secret 行自登记起从不修改 created_at，
未激活的回滚更不会换引用——连接继续持旧凭据，其账龄以旧 Secret 的
created_at 连续计算，不会因轮换尝试而刷新（FR-07 / FR-10 预警口径一致）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import (
    DataConnection,
    SecretRotationRequest,
    SecretRotationRollbackRequest,
)
from etl_agent.secrets.errors import SecretNotFound, SecretStateConflict
from etl_agent.secrets.rotation import (
    FAILED,
    PRE_ACTIVATION_STATES,
    _load_rotation,
)

# 回滚申请状态：S3 为同步执行（换引用前中止，无异步步骤），落地即 COMPLETED
ROLLBACK_PENDING = "PENDING"
ROLLBACK_COMPLETED = "COMPLETED"
ROLLBACK_FAILED = "FAILED"


async def rollback_rotation(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    rotation_id: uuid.UUID,
    requested_by: uuid.UUID,
    reason: str | None,
) -> SecretRotationRollbackRequest:
    """回滚轮换（FR-07）：未激活即中止，旧凭据原样保留。

    仅允许激活前状态（VALIDATING/WAITING_APPROVAL/APPROVED）——
    已激活（SUCCEEDED）的轮换不在此路径回滚（旧凭据引用已切换，需走重新轮换）。

    Raises:
        SecretStateConflict: 轮换已激活或已终结。
    """
    rotation = await _load_rotation(session, project_id, rotation_id)
    if rotation.status not in PRE_ACTIVATION_STATES:
        raise SecretStateConflict(
            f"轮换状态 {rotation.status} 不可回滚（仅激活前可回滚）"
        )
    # 旧凭据原样保留：不换 data_connections.secret_ref、不刷新任何时间戳（FR-07）
    rotation.status = FAILED
    rotation.failure_reason = f"rolled back before activation: {reason or 'no reason given'}"
    rotation.completed_at = datetime.now(UTC)

    rollback = SecretRotationRollbackRequest(
        rotation_request_id=rotation.id,
        status=ROLLBACK_COMPLETED,
        requested_by=requested_by,
        reason=reason,
        completed_at=datetime.now(UTC),
    )
    session.add(rollback)
    await session.flush()
    return rollback


async def load_rollback(
    session: AsyncSession, project_id: uuid.UUID, rollback_id: uuid.UUID
) -> SecretRotationRollbackRequest:
    """按项目加载回滚申请（经 rotation → connection 隔离，AC-12）。"""
    result = await session.execute(
        select(SecretRotationRollbackRequest)
        .join(
            SecretRotationRequest,
            SecretRotationRequest.id == SecretRotationRollbackRequest.rotation_request_id,
        )
        .join(DataConnection, DataConnection.id == SecretRotationRequest.connection_id)
        .where(
            SecretRotationRollbackRequest.id == rollback_id,
            DataConnection.project_id == project_id,
        )
    )
    rollback = result.scalar_one_or_none()
    if rollback is None:
        raise SecretNotFound("回滚申请不存在")
    return rollback
