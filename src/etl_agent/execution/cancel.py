"""M-K 执行与数据面 — 执行取消（CANCELLING→CANCELLED，FR-49）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 execution_runs / 第 3.2 章 M-J/M-K
覆盖 FR：FR-49（执行取消：CANCELLING→CANCELLED，记录取消原因）、FR-37（CANCEL_EXECUTION 副作用）
对应 AC：AC-06（取消链路可观测）、AC-08 前置（硬中断 CANCEL 在 S8 监督链路）

S7 边界：取消**申请与确认**在本模块（RUNNING→CANCELLING 记录原因，
引擎侧作业终止确认后 →CANCELLED）；发起取消的对外入口是副作用准备单
（CANCEL_EXECUTION，API-13 属 S8），本模块不提供 HTTP 端点——
控制面只实现状态推进与原因留痕（提示词 §8.2：S7 不做监督硬中断）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.errors import ExecutionStateConflict
from etl_agent.execution.service import (
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_RUNNING,
    STATUS_SUBMITTED,
    is_terminal,
    load_run,
    record_event,
    transition,
)


async def request_cancel(
    session: AsyncSession, run_id: uuid.UUID, *, reason: str
) -> tuple[str, str]:
    """申请取消：RUNNING/SUBMITTED → CANCELLING（FR-49）。

    两段式取消的原因：引擎侧作业终止是异步的（SeaTunnel stop-job 返回后作业
    仍在收尾），先落 CANCELLING 让监控可观测「取消已申请」，终止确认后再落
    CANCELLED——避免运行在引擎侧仍 SAFE_POINT 时被误判为已终止。

    Args:
        reason: 取消原因（留痕；不含凭据 / HOCON 正文，AC-02）。

    Returns:
        (运行状态, 取消原因) 元组。

    Raises:
        ExecutionStateConflict: 运行已是终态或已 CANCELLING（重复申请幂等拒绝）。
    """
    run = await load_run(session, run_id)
    if run.status == STATUS_CANCELLING:
        return run.status, reason  # 幂等：重复申请不重复迁移
    if is_terminal(run.status):
        raise ExecutionStateConflict(f"执行已终态 {run.status}，不可取消（FR-49）")
    if run.status not in {STATUS_RUNNING, STATUS_SUBMITTED}:
        raise ExecutionStateConflict(f"执行状态 {run.status} 不可取消（FR-49）")
    await transition(session, run, STATUS_CANCELLING, reason=f"取消申请：{reason}")
    await record_event(
        session,
        run.id,
        level="WARN",
        event_type="execution.cancel_requested",
        message=f"取消原因：{reason}",
        stage="cancel",
        occurred_at=datetime.now(UTC),
    )
    return run.status, reason


async def confirm_cancel(session: AsyncSession, run_id: uuid.UUID) -> None:
    """确认取消：CANCELLING → CANCELLED（引擎侧作业终止已确认，FR-49）。"""
    run = await load_run(session, run_id)
    if run.status == STATUS_CANCELLED:
        return  # 幂等
    if run.status != STATUS_CANCELLING:
        raise ExecutionStateConflict(f"执行状态 {run.status} 不可确认取消（FR-49）")
    await transition(session, run, STATUS_CANCELLED, reason="取消已确认：引擎侧作业已终止")
    await session.commit()
