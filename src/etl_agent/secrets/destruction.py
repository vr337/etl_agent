"""M-C 凭据治理 — 凭据销毁（T-06.3 / FR-09）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / 第 8 章「安全 · 凭据保护」行
覆盖 FR：FR-09（轮换源/回滚暂存两类目标的资格判定与销毁申请，留墓碑）
对应 AC：AC-02（墓碑不含秘密：密文与定位引用一律清空）

资格判定（S3 提示词 9.9）：
- ROLLBACK_STAGED：某轮换暂存的新凭据，且该轮换已 FAILED（未激活即中止）→ 可销毁；
- ROTATION_SOURCE：不被任何连接引用、也不是任何未完结轮换暂存的「旧凭据」
  （被成功轮换/迁移替换下来的那一枚）→ 可销毁；
- 其他情形（如在用凭据）→ 不可销毁，eligibility_note 说明原因。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.connections.service import load_connection
from etl_agent.infrastructure.models import (
    DataConnection,
    EncryptedSecret,
    SecretDestructionRequest,
    SecretRotationRequest,
)
from etl_agent.secrets.errors import SecretNotFound
from etl_agent.secrets.factory import create_provider

# 销毁目标类型（S1 模型 target_type 取值）
ROTATION_SOURCE = "ROTATION_SOURCE"
ROLLBACK_STAGED = "ROLLBACK_STAGED"

# 销毁申请状态
DESTRUCTION_PENDING = "PENDING"
DESTRUCTION_DESTROYED = "DESTROYED"
DESTRUCTION_REJECTED = "REJECTED"


async def _referenced_secret_ids(session: AsyncSession) -> tuple[set[uuid.UUID], set[uuid.UUID], set[uuid.UUID]]:
    """全库引用关系：(在用, 进行中暂存, 已中止暂存) 三个 secret_id 集合。

    - 在用：连接当前引用的 secret_ref；
    - 进行中暂存：未完结轮换（VALIDATING/WAITING_APPROVAL/APPROVED/ACTIVATING）的 new_secret_ref；
    - 已中止暂存：回滚/校验失败（FAILED）轮换的 new_secret_ref——可销毁（FR-07/09）。
    """
    in_use = {
        sid
        for sid in (
            await session.execute(
                select(DataConnection.secret_ref).where(DataConnection.secret_ref.is_not(None))
            )
        )
        .scalars()
        .all()
        if sid is not None
    }
    rows = (
        await session.execute(
            select(SecretRotationRequest.new_secret_ref, SecretRotationRequest.status)
            .join(
                DataConnection,
                DataConnection.id == SecretRotationRequest.connection_id,
            )
            .where(SecretRotationRequest.new_secret_ref.is_not(None))
        )
    ).all()
    active_staged: set[uuid.UUID] = set()
    aborted_staged: set[uuid.UUID] = set()
    for secret_ref, status in rows:
        if secret_ref is None:
            continue
        if status in {"VALIDATING", "WAITING_APPROVAL", "APPROVED", "ACTIVATING"}:
            active_staged.add(secret_ref)
        elif status == "FAILED":
            aborted_staged.add(secret_ref)
    return in_use, active_staged, aborted_staged


async def request_destruction(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    secret_id: uuid.UUID,
    target_type: str,
    reason: str | None,
    requested_by: uuid.UUID,
    settings: Settings,
) -> SecretDestructionRequest:
    """销毁申请 + 资格判定（FR-09）。

    路径上的 connection_id 提供项目隔离锚点（AC-12：跨项目视同不存在）；
    secret_id 必须与该连接的历史相关（当前凭据 / 其轮换暂存 / 被替换的旧凭据）。

    Returns:
        SecretDestructionRequest: eligible 反映资格判定；不合格时 status=REJECTED。
    """
    await load_connection(session, project_id, connection_id)
    result = await session.execute(
        select(EncryptedSecret).where(EncryptedSecret.id == secret_id)
    )
    secret = result.scalar_one_or_none()
    if secret is None:
        raise SecretNotFound("Secret 引用不存在")

    eligible, note = await _evaluate(session, secret_id=secret_id, target_type=target_type)
    request = SecretDestructionRequest(
        secret_id=secret_id,
        target_type=target_type,
        eligible=eligible,
        eligibility_note=note,
        status=DESTRUCTION_PENDING if eligible else DESTRUCTION_REJECTED,
        requested_by=requested_by,
        reason=reason,
    )
    session.add(request)
    await session.flush()
    return request


async def execute_destruction(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    destruction_id: uuid.UUID,
    settings: Settings,
) -> SecretDestructionRequest:
    """执行已批准的销毁（FR-09）：Provider 删除密钥材料 + 墓碑（不含秘密）。"""
    result = await session.execute(
        select(SecretDestructionRequest).where(SecretDestructionRequest.id == destruction_id)
    )
    request = result.scalar_one_or_none()
    if request is None:
        raise SecretNotFound("销毁申请不存在")
    if not request.eligible or request.status != DESTRUCTION_PENDING:
        from etl_agent.secrets.errors import SecretStateConflict

        raise SecretStateConflict("销毁申请不可执行（资格不符或已处理）")
    provider = create_provider(session, settings)
    await provider.destroy(request.secret_id)
    request.status = DESTRUCTION_DESTROYED
    request.destroyed_at = datetime.now(UTC)
    await session.flush()
    return request


async def _evaluate(
    session: AsyncSession, *, secret_id: uuid.UUID, target_type: str
) -> tuple[bool, str]:
    """资格判定（FR-09 / S3 提示词 9.9）。"""
    in_use, active_staged, aborted_staged = await _referenced_secret_ids(session)
    if secret_id in in_use:
        return False, "凭据仍在用（连接当前引用）"
    if target_type == ROLLBACK_STAGED:
        if secret_id in aborted_staged:
            return True, "回滚暂存（未激活已中止）可销毁"
        return False, "非暂存凭据或轮换尚未中止（进行中暂存不可销毁）"
    if target_type == ROTATION_SOURCE:
        if secret_id in active_staged:
            return False, "该凭据是进行中轮换的暂存目标，不能按轮换源销毁"
        return True, "轮换源（旧凭据，已不被引用）可销毁"
    return False, f"未知目标类型: {target_type}"
