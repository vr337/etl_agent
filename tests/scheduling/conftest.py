"""S7 调度域共享夹具：项目 A 下的管线（调度策略的唯一性锚点）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 pipeline_schedules（pipeline_id UK）
覆盖 FR：FR-40（一管线一调度策略）
对应 AC：AC-06（调度策略可治理）
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import Pipeline
from tests.helpers import SeededWorkspace


@pytest.fixture
async def seeded_pipeline(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> uuid.UUID:
    """项目 A 下的一条管线（调度策略按 pipeline_id 唯一绑定）。"""
    pipeline = Pipeline(project_id=seeded.project_a_id, name=f"sched-{uuid.uuid4().hex[:8]}")
    db_session.add(pipeline)
    await db_session.flush()
    return pipeline.id


@pytest.fixture
async def seeded_pipeline_b(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> uuid.UUID:
    """项目 B 下的一条管线（跨项目隔离用例）。"""
    pipeline = Pipeline(project_id=seeded.project_b_id, name=f"sched-b-{uuid.uuid4().hex[:8]}")
    db_session.add(pipeline)
    await db_session.flush()
    return pipeline.id
