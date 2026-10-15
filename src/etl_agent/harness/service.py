"""M-J Harness 安全执行内核 — 跨子域共享服务（路由编排用）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 6.1 章 API-08/09/10
覆盖 FR：FR-31（准备单）、FR-66（项目隔离）
对应 AC：AC-05（prepare→approve→commit）、AC-12（跨项目 403）

只放路由编排所需的轻量查询；安全判决全部在 prepare / approve / commit 服务内。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.errors import PreparationNotFound
from etl_agent.infrastructure.models import ExecutionPreparation


async def load_preparation_project(
    session: AsyncSession, preparation_id: uuid.UUID
) -> uuid.UUID:
    """反查准备单所属项目（API-09/10 路径无 `project_id`，需回溯项目做鉴权）。

    Raises:
        PreparationNotFound: 准备单不存在（BIZ_404_NOT_FOUND，不泄露存在性）。
    """
    row = (
        await session.execute(
            select(ExecutionPreparation.project_id).where(
                ExecutionPreparation.id == preparation_id
            )
        )
    ).one_or_none()
    if row is None:
        raise PreparationNotFound("准备单不存在")
    return row[0]
