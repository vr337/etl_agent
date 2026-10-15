"""执行重试用例（T-13.14 / FR-42）。

覆盖：稳定 SeaTunnel JobId（同一版本恒定）、`retry_of_run_id` / `attempt_number` 追溯链、
重试必须重新授权（不复用旧令牌，ADR-02）。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.retry import (
    build_retry_context,
    next_attempt_number,
    stable_job_id,
)
from tests.execution.conftest import RunFactory


def test_stable_job_id_is_version_scoped() -> None:
    """同一版本恒定、不同版本不同（引擎侧据此识别为同一作业，FR-42）。"""
    version_id = uuid.uuid4()
    assert stable_job_id(version_id) == f"etl-job-{version_id}"
    assert stable_job_id(version_id) == stable_job_id(version_id)
    assert stable_job_id(uuid.uuid4()) != stable_job_id(version_id)


async def test_retry_context_points_to_chain_root(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """首个重试的 `retry_of_run_id` 指向被重试运行，序号从 2 起（头运行 attempt=1）。"""
    original = await make_run(status="FAILED")

    context = await build_retry_context(db_session, original)

    assert context["retry_of_run_id"] == original.id
    assert context["attempt_number"] == 2
    assert context["job_id"] == stable_job_id(original.pipeline_version_id)


async def test_retry_chain_stays_flat(db_session: AsyncSession, make_run: RunFactory) -> None:
    """链式追溯而非指数展开：后续重试仍指向同一头运行，序号递增。"""
    root = await make_run(status="FAILED")
    assert await next_attempt_number(db_session, root.id) == 2

    first_retry = await make_run(status="FAILED")
    first_retry.retry_of_run_id = root.id
    first_retry.attempt_number = 2
    await db_session.flush()

    context = await build_retry_context(db_session, first_retry)

    assert context["retry_of_run_id"] == root.id
    assert context["attempt_number"] == 3


async def test_retry_does_not_reuse_token(db_session: AsyncSession, make_run: RunFactory) -> None:
    """重试不携带旧令牌上下文（重试必须重新 prepare→approve→commit，ADR-02）。"""
    original = await make_run(status="FAILED")
    context = await build_retry_context(db_session, original)
    assert set(context) == {"retry_of_run_id", "attempt_number", "job_id"}
    assert "token" not in " ".join(context).lower()
