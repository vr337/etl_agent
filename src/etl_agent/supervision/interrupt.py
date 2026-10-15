"""M-L 运行时监督 — 硬中断（CANCEL 引擎作业，FR-46，AC-08 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 7.2 章异常流程时序图 / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-46（减损动作 CANCEL：硬中断）、FR-49（取消链路 CANCELLING→CANCELLED）
对应 AC：AC-08（超预算 → 硬中断 → 失败快照 + 根因诊断）

流程（提示词 §8.7，顺序即安全边界）：

```
1. RUNNING/SUBMITTED → CANCELLING        （先落「取消已申请」，让监控可见）
2. 调 SeaTunnel stop-job（独立重试）      （外部动作失败不阻塞后续）
3. CANCELLING → CANCELLED                （终态，不可逆）
4. 落失败分类 CANCELLATION + 根因诊断     （可解释的处置结论）
```

**为什么先迁状态再停引擎**：反过来的话，stop-job 成功但进程在迁状态前崩溃，
运行会永远停在 RUNNING 而引擎已停——「假活运行」比「取消未确认」难查得多。
先落 CANCELLING 则崩溃后停在 CANCELLING，运维一眼能看出「取消已申请但未确认」。

**为什么外部失败不阻塞**：stop-job 失败只意味着引擎可能还在跑，
但「运行已被监督判定必须中断」这一事实必须落库（禁止事项 18）；
重试轨迹由 `external_mitigation` 承载，运维据此决定是否人工介入。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.errors import ExecutionStateConflict
from etl_agent.execution.service import (
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUBMITTED,
    is_terminal,
    transition,
)
from etl_agent.infrastructure.models import ExecutionRun
from etl_agent.supervision.external_mitigation import (
    DEFAULT_MAX_ATTEMPTS,
    ExternalMitigationResult,
    apply_with_retry,
    not_required,
    record_external_mitigation,
)

# 硬中断的失败分类（诊断域 9 类之一，S8 提示词 §8.10）
FAILURE_CATEGORY_CANCELLATION = "CANCELLATION"
FAILURE_STAGE_CANCEL = "cancel"
EXTERNAL_ACTION_STOP_JOB = "STOP_ENGINE_JOB"


@dataclass(frozen=True)
class InterruptOutcome:
    """硬中断结果（interrupted=False 时 reason 说明为何未中断）。"""

    interrupted: bool
    status: str
    reason: str
    external: ExternalMitigationResult | None = None


async def hard_interrupt(
    session: AsyncSession,
    run: ExecutionRun,
    *,
    engine: object | None,
    reason: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> InterruptOutcome:
    """硬中断一次执行（FR-46）：迁状态 → 停引擎（独立重试）→ 终态 → 诊断。

    Args:
        session: Worker / 请求级会话（调用方负责提交）。
        run: 目标运行。
        engine: SeaTunnel 客户端（None 表示不调引擎，只落治理事实——测试 / 无引擎环境）。
        reason: 中断原因（写入事件流；不含凭据与栈正文）。
        max_attempts: 外部动作重试预算（默认 3）。

    Returns:
        InterruptOutcome: 是否中断、终态与外部动作结果（外部失败不影响 interrupted）。
    """
    from etl_agent.diagnosis.service import apply_diagnosis

    if is_terminal(run.status):
        return InterruptOutcome(
            interrupted=False,
            status=run.status,
            reason=f"运行已是终态 {run.status}，硬中断幂等跳过",
        )

    # 1. 先落「取消已申请」（崩溃后可诊断；见模块 docstring）
    if run.status in {STATUS_RUNNING, STATUS_SUBMITTED}:
        await transition(
            session, run, STATUS_CANCELLING, reason=f"监督硬中断：{_brief(reason)}"
        )
    elif run.status != STATUS_QUEUED:
        raise ExecutionStateConflict(f"执行状态 {run.status} 不可硬中断（FR-46）")

    # 2. 停引擎（独立重试；失败不阻塞）
    external = await _stop_engine(session, run, engine=engine, max_attempts=max_attempts)

    # 3. 落终态（CANCELLED 不可逆）
    await transition(
        session,
        run,
        STATUS_CANCELLED,
        reason=f"监督硬中断已确认：{_brief(reason)}",
        failure_category=FAILURE_CATEGORY_CANCELLATION,
        failure_stage=FAILURE_STAGE_CANCEL,
        retryable=False,
    )
    # 4. 根因诊断（可解释的处置结论；失败分类固定为 CANCELLATION）
    await apply_diagnosis(
        session,
        run,
        failure_category=FAILURE_CATEGORY_CANCELLATION,
        failure_stage=FAILURE_STAGE_CANCEL,
        message=reason,
    )
    return InterruptOutcome(
        interrupted=True,
        status=run.status,
        reason=f"硬中断完成：{_brief(reason)}",
        external=external,
    )


async def _stop_engine(
    session: AsyncSession,
    run: ExecutionRun,
    *,
    engine: object | None,
    max_attempts: int,
) -> ExternalMitigationResult:
    """调用引擎 stop-job（无引擎 / 无作业号时记 NOT_REQUIRED）。"""
    job_id = run.seatunnel_job_id
    if engine is None or not job_id:
        result = not_required(EXTERNAL_ACTION_STOP_JOB)
    else:

        async def _call() -> None:
            await engine.stop_job(job_id)  # type: ignore[attr-defined]

        result = await apply_with_retry(
            EXTERNAL_ACTION_STOP_JOB, _call, max_attempts=max_attempts
        )
    await record_external_mitigation(session, run.id, result)
    run.engine_status = "CANCELED" if result.status != "FAILED" else run.engine_status
    await session.flush()
    return result


def _brief(text: str, limit: int = 200) -> str:
    """摘要截断（事件流只落治理事实，不含栈正文，AC-02 / 禁止事项 16）。"""
    return " ".join(text.split())[:limit]


__all__ = [
    "EXTERNAL_ACTION_STOP_JOB",
    "FAILURE_CATEGORY_CANCELLATION",
    "FAILURE_STAGE_CANCEL",
    "InterruptOutcome",
    "hard_interrupt",
]