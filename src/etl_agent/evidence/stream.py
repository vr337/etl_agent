"""M-N 审计证据 — 证据流创建与获取（按 project_id）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_streams
覆盖 FR：FR-36（每个项目一条只追加证据流，head_hash 为链头）
对应 AC：AC-12（多租户隔离：跨项目视同不存在）、AC-09（证据覆盖率）

**一个项目一条流**：`harness_evidence_streams` 对 `project_id` 建索引但未建唯一约束（S1 模型），
故「唯一」由本模块的 `get_or_create_stream` 保证——并发首次写入时靠
「先查后插 + 唯一冲突回查」收敛，不依赖 DDL 变更（S9 不新增迁移）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.evidence.errors import EvidenceStreamNotFound
from etl_agent.infrastructure.models import HarnessEvidenceStream


async def get_stream_by_project(
    session: AsyncSession, project_id: uuid.UUID
) -> HarnessEvidenceStream | None:
    """按项目取证据流（不存在返回 None，由调用方决定是否创建）。"""
    result = await session.execute(
        select(HarnessEvidenceStream).where(HarnessEvidenceStream.project_id == project_id)
    )
    return result.scalars().first()


async def get_or_create_stream(
    session: AsyncSession, project_id: uuid.UUID
) -> HarnessEvidenceStream:
    """取项目的证据流，不存在则创建（幂等）。

    并发场景下两个请求可能同时走到插入：`IntegrityError` 说明另一个事务已建流，
    回查返回既有行即可——重复建流会让同一项目出现两条链，等于证据账本被静默分叉。
    """
    existing = await get_stream_by_project(session, project_id)
    if existing is not None:
        return existing
    stream = HarnessEvidenceStream(project_id=project_id, head_hash=None)
    session.add(stream)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        retried = await get_stream_by_project(session, project_id)
        if retried is None:
            raise
        return retried
    return stream


async def require_stream(session: AsyncSession, project_id: uuid.UUID) -> HarnessEvidenceStream:
    """取项目证据流，不存在即 404（不自动创建——查询语义不该产生副作用）。"""
    stream = await get_stream_by_project(session, project_id)
    if stream is None:
        raise EvidenceStreamNotFound("证据流不存在")
    return stream


async def get_stream_by_id(
    session: AsyncSession, stream_id: uuid.UUID
) -> HarnessEvidenceStream:
    """按流 ID 取证据流，不存在即 404。"""
    stream = await session.get(HarnessEvidenceStream, stream_id)
    if stream is None:
        raise EvidenceStreamNotFound("证据流不存在")
    return stream


__all__ = [
    "get_or_create_stream",
    "get_stream_by_id",
    "get_stream_by_project",
    "require_stream",
]
