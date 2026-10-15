"""M-K 执行与数据面 — 执行重试（稳定 JobId + retry_of_run_id，FR-42）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 execution_runs（retry_of_run_id /
attempt_number / seatunnel_job_id）/ ADR-06 消费侧幂等
覆盖 FR：FR-42（执行重试：稳定 SeaTunnel JobId；retry_of_run_id / attempt_number 追溯）
对应 AC：AC-06（重试链路可追溯）、AC-05 前置（重试仍走 Harness 授权，不自动补令牌）

幂等三件套（SPEC 8「高可用」消费侧幂等行）：
1. **稳定 SeaTunnel JobId**：同一版本同一管线的 JobId 恒定（引擎侧重复提交可识别为
   同一作业，恢复而非重复搬运）；
2. `retry_of_run_id`：重试运行指向被重试的前序运行（追溯链）；
3. `attempt_number`：递增尝试序号（同一前序链上计数）。

边界（ADR-02 / 禁止事项 19）：重试运行仍是**新准备单**的新一次授权——
`harness_preparation_id` 在一运行一准备单的唯一约束下，不存在"旧令牌复用重试"，
重试必须重新 prepare→approve→commit（那才签发新单次令牌）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import ExecutionRun


def stable_job_id(pipeline_version_id: uuid.UUID) -> str:
    """稳定 SeaTunnel JobId（FR-42：同一版本恒定，重试复用）。"""
    return f"etl-job-{pipeline_version_id}"


async def next_attempt_number(session: AsyncSession, retry_of_run_id: uuid.UUID) -> int:
    """同一前序链上的下一个尝试序号（FR-42 attempt_number）。

    口径：重试链的「头」是被重试的运行；后续重试都指向同一头（链式追溯而非
    指数展开），序号 = 链上已有运行数 + 1。
    """
    result = await session.execute(
        select(func.count())
        .select_from(ExecutionRun)
        .where(ExecutionRun.retry_of_run_id == retry_of_run_id)
    )
    count = int(result.scalar() or 0)
    return count + 2  # 头运行自身 attempt_number=1，首个重试=2


async def build_retry_context(
    session: AsyncSession, run: ExecutionRun
) -> dict[str, object]:
    """为重试运行构造追溯上下文（API-11 重试入参装配，FR-42）。

    Returns:
        `{"retry_of_run_id": 链头运行 ID, "attempt_number": 下一序号, "job_id": 稳定 JobId}`
    """
    root = run.retry_of_run_id or run.id
    return {
        "retry_of_run_id": root,
        "attempt_number": await next_attempt_number(session, root),
        "job_id": stable_job_id(run.pipeline_version_id),
    }
