"""M-H 不可变版本与设计审批 — 版本生命周期服务（派生 / 状态迁移 / 乐观锁）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H / 第 5.2 章 pipeline_versions / 第 8 章「可维护 · 迁移可回滚」
覆盖 FR：FR-26（版本链状态机、派生 parent_version_id、乐观锁 lock_version、禁原地覆盖）
对应 AC：AC-04（不可变版本）

实现口径：
- **乐观锁**：所有写入走 `UPDATE ... WHERE lock_version = :expected` + `lock_version = lock_version + 1`，
  rowcount=0 即并发冲突 → `BIZ_409_STATE_CONFLICT`（SPEC 8「高可用」：并发修改以冲突重试）；
- **派生**：新版本 `version_number = max+1`、`status=DRAFT`、`parent_version_id` 指向源版本；
  派生版本**不复制制品摘要**——候选正文在 `etl_checkpoint`（S5 吸收 2 决议），
  派生后重新生成即可产出自己的制品，避免父子制品行混淆。
"""
from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import Pipeline, PipelineVersion
from etl_agent.versions.errors import VersionNotFound, VersionStateConflict
from etl_agent.versions.state import (
    STATUS_DRAFT,
    can_transition,
    is_frozen,
)


async def load_version(
    session: AsyncSession, version_id: uuid.UUID, project_id: uuid.UUID | None = None
) -> tuple[PipelineVersion, uuid.UUID]:
    """加载版本与其所属项目；给 `project_id` 时按项目隔离（跨项目视同不存在）。

    Raises:
        VersionNotFound: 版本不存在或不属于该项目（BIZ_404_NOT_FOUND，不泄露存在性）。
    """
    stmt = (
        select(PipelineVersion, Pipeline.project_id)
        .join(Pipeline, PipelineVersion.pipeline_id == Pipeline.id)
        .where(PipelineVersion.id == version_id)
    )
    if project_id is not None:
        stmt = stmt.where(Pipeline.project_id == project_id)
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise VersionNotFound("Pipeline 版本不存在或不属于当前项目")
    return row[0], row[1]


async def derive_version(
    session: AsyncSession, source_version_id: uuid.UUID, *, project_id: uuid.UUID
) -> PipelineVersion:
    """派生新版本（FR-26：修改必须派生，禁原地覆盖）。

    Args:
        session: 请求级数据库会话。
        source_version_id: 源版本（可处于任意状态，含 APPROVED/REJECTED 终态）。
        project_id: 隔离边界。

    Returns:
        PipelineVersion: 新版本（DRAFT，version_number = 同 Pipeline 内 max+1）。

    Raises:
        VersionNotFound: 源版本不存在或跨项目。
    """
    source, _ = await load_version(session, source_version_id, project_id)
    next_number = (
        await session.execute(
            select(func.coalesce(func.max(PipelineVersion.version_number), 0)).where(
                PipelineVersion.pipeline_id == source.pipeline_id
            )
        )
    ).scalar_one() + 1
    derived = PipelineVersion(
        pipeline_id=source.pipeline_id,
        version_number=int(next_number),
        status=STATUS_DRAFT,
        parent_version_id=source.id,
        lock_version=0,
    )
    session.add(derived)
    await session.flush()
    return derived


async def mark_generated(
    session: AsyncSession, version_id: uuid.UUID, *, expected_lock_version: int | None = None
) -> PipelineVersion:
    """标记版本已产出候选（DRAFT/PROFILING → GENERATED；已在 GENERATED 则幂等返回）。

    Raises:
        VersionNotFound:       版本不存在。
        VersionStateConflict:  已冻结版本（VALIDATED 及以后）不允许再生成，或乐观锁冲突。
    """
    version = await session.get(PipelineVersion, version_id)
    if version is None:
        raise VersionNotFound("Pipeline 版本不存在")
    if version.status == "GENERATED":
        return version
    if is_frozen(version.status):
        raise VersionStateConflict(
            f"版本状态 {version.status} 已冻结，不可原地覆盖；请派生新版本（FR-26）"
        )
    return await transition(session, version, "GENERATED", expected_lock_version=expected_lock_version)


async def transition(
    session: AsyncSession,
    version: PipelineVersion,
    target: str,
    *,
    expected_lock_version: int | None = None,
) -> PipelineVersion:
    """执行状态迁移（走 `TRANSITIONS` 白名单 + 乐观锁）。

    Args:
        session: 请求级数据库会话。
        version: 目标版本（已加载）。
        target: 目标状态。
        expected_lock_version: 期望的 `lock_version`；给定时启用乐观锁比对。

    Returns:
        PipelineVersion: 迁移后的版本（`lock_version` 已 +1）。

    Raises:
        VersionStateConflict: 迁移不在白名单内，或乐观锁冲突（rowcount=0）。
    """
    if not can_transition(version.status, target):
        raise VersionStateConflict(
            f"版本状态 {version.status} → {target} 不是合法迁移（FR-26 版本链）"
        )
    return await update_with_lock(
        session, version, expected_lock_version=expected_lock_version, status=target
    )


async def update_with_lock(
    session: AsyncSession,
    version: PipelineVersion,
    *,
    expected_lock_version: int | None = None,
    **fields: Any,
) -> PipelineVersion:
    """带乐观锁的字段更新（`WHERE lock_version = :expected`，成功则 +1）。

    Raises:
        VersionStateConflict: `expected_lock_version` 与库内值不一致（并发修改）。
    """
    expected = version.lock_version if expected_lock_version is None else expected_lock_version
    statement = (
        update(PipelineVersion)
        .where(PipelineVersion.id == version.id, PipelineVersion.lock_version == expected)
        .values(lock_version=expected + 1, **fields)
    )
    result = cast("CursorResult[Any]", await session.execute(statement))
    affected: int = result.rowcount or 0
    if affected != 1:
        # 乐观锁冲突：期间已被其他事务修改（SPEC 8「高可用」并发修改以冲突重试）
        raise VersionStateConflict("版本已被并发修改（lock_version 冲突），请重新读取后重试")
    await session.refresh(version)
    return version
