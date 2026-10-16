"""M-K 质量与发布 — 发布门禁与发布状态机（FR-43/44，AC-07 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 6.1 章 API-14 / 第 8 章「安全 · 数据面发布」
覆盖 FR：FR-43（原子 Swap 的同 Schema 强校验与发布时机）、FR-44（质量报告 PASSED 才允许发布）、
        FR-46（QUARANTINE 阻断后续质量发布与原子交换）
对应 AC：AC-07（质量 PASSED → Swap → 正式表发布）、AC-08（隔离后不得发布）

**四道门禁**（缺一不可，顺序即优先级）：

| 序 | 门禁 | 失败码 | 判定位置 |
|----|------|--------|---------|
| 1 | 未被 QUARANTINE（`quarantined_at` / `supervision_action`） | `BIZ_422_SWAP_QUARANTINED` | `evaluate_publish_gate` |
| 2 | 质量报告 `PASSED` | `BIZ_422_SWAP_QUALITY_FAILED` | `evaluate_publish_gate` |
| 3 | 影子表与正式表 Schema 一致 | `BIZ_422_SWAP_SCHEMA_MISMATCH` | `execute_atomic_swap`（swap 前读两侧 `DESC` 逐列比对） |
| 4 | 幂等：未发布过 | —（直接返回已发布） | `publish_run`（由事件流派生） |

**为什么第 3 道不在门禁函数里**（T-SHADOW-01 PR-B 修正说明）：本 PR 前 docstring 误称
「四道都在 `evaluate_publish_gate`」，实际该函数只判 1、2 道，且白收了一个未使用的
`executor` 参数。**本 PR 只做最小修正**（删僵尸参数 + 让文档说真话），不把 schema 校验
挪进门禁函数——那会让 Doris 被读两次，且属重构，不在「接线」范围内。
收拢到一处的后续事项见 tracking 的 **T-PUBLISH-01**。

**发布状态机的状态载体**：SPEC 5.2 的 `execution_runs` 没有发布状态列，故状态由
**事件流**（`execution.publish_*`）承载并由 `current_publish_state()` 派生——
事件流是审计级事实源，比新增一列更符合「控制面只存治理事实」的口径（禁止事项 7 不改契约）。
注意：派生状态**仅供本模块幂等判断使用，尚未对外暴露**（见 tracking 的 **T-OBS-01**）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.atomic_swap import (
    SwapExecutor,
    SwapResult,
    execute_atomic_swap,
)
from etl_agent.execution.errors import (
    SwapQualityFailed,
    SwapQuarantined,
)
from etl_agent.execution.routing import STATUS_PASSED
from etl_agent.execution.service import record_event
from etl_agent.infrastructure.models import ExecutionEvent, ExecutionRun, QualityReport

# 发布状态（由事件流派生，见模块 docstring）
STATE_SHADOW_WRITTEN = "SHADOW_WRITTEN"
STATE_READY = "READY"
STATE_PUBLISHED = "PUBLISHED"
STATE_BLOCKED = "BLOCKED"

# 发布事件类型（状态机的唯一事实源）
EVENT_PUBLISHED = "execution.published"
EVENT_BLOCKED = "execution.publish_blocked"
EVENT_FAILED = "execution.publish_failed"

# 隔离动作（与 supervision.mitigation 的动作常量同值；此处不反向依赖 supervision，
# 避免 execution 域依赖监督域造成循环）
ACTION_QUARANTINE = "QUARANTINE"


@dataclass(frozen=True)
class PublishTargets:
    """发布目标（正式表 / 影子表 / 错误表 + 质量契约）。"""

    target_table: str
    shadow_table: str
    error_table: str | None
    quality_contract: dict[str, Any]


@dataclass(frozen=True)
class PublishDecision:
    """门禁裁决（allowed=False 时 code 即对外错误码）。"""

    allowed: bool
    reason: str
    code: str | None = None


@dataclass(frozen=True)
class PublishOutcome:
    """发布结果（published=True 且 blocked=False 才算真正发布成功）。"""

    published: bool
    blocked: bool
    state: str
    reason: str
    code: str | None = None
    executed_sql: str | None = None


def resolve_targets(plan: dict[str, Any] | None) -> PublishTargets | None:
    """从 ETL 计划解析发布目标（无 sink.table 即无发布动作，返回 None）。"""
    if not isinstance(plan, dict):
        return None
    sink = plan.get("sink")
    target = sink.get("table") if isinstance(sink, dict) else None
    if not target:
        return None
    contract = plan.get("quality_contract")
    contract_dict = contract if isinstance(contract, dict) else {}
    valid_sink = contract_dict.get("valid_sink")
    error_sink = contract_dict.get("error_sink")
    return PublishTargets(
        target_table=str(target),
        shadow_table=str(valid_sink) if valid_sink else f"{target}_shadow",
        error_table=str(error_sink) if error_sink else None,
        quality_contract=contract_dict,
    )


def is_quarantined(run: ExecutionRun) -> bool:
    """运行是否处于隔离态（时间戳或监督动作任一命中即隔离）。"""
    return bool(run.quarantined_at) or run.supervision_action == ACTION_QUARANTINE


async def current_publish_state(session: AsyncSession, run_id: uuid.UUID) -> str:
    """派生当前发布状态（事件流为唯一事实源）。"""
    events = (
        (
            await session.execute(
                select(ExecutionEvent.event_type).where(ExecutionEvent.run_id == run_id)
            )
        )
        .scalars()
        .all()
    )
    types = set(events)
    if EVENT_PUBLISHED in types:
        return STATE_PUBLISHED
    if EVENT_BLOCKED in types:
        return STATE_BLOCKED
    return STATE_READY


async def evaluate_publish_gate(
    session: AsyncSession,
    run: ExecutionRun,
    *,
    report: QualityReport | None,
    targets: PublishTargets,
) -> PublishDecision:
    """门禁裁决（第 1、2 道；不执行任何副作用，失败只返回裁决，不抛异常）。

    `targets` 是**决策上下文**（要发布什么），故保留；`executor` 是执行通道依赖，
    属错误分层，T-SHADOW-01 PR-B 已删（原先是白收未用的僵尸参数）。

    第 3 道（Schema 一致）在 `execute_atomic_swap` 内判——它必须读两侧 `DESC`，
    属执行期动作；第 4 道（幂等）在 `publish_run` 判。见模块 docstring 的门禁分布表。
    """
    if is_quarantined(run):
        return PublishDecision(
            allowed=False,
            code=SwapQuarantined.code,
            reason="运行处于 QUARANTINE，禁止原子发布（AC-07/AC-08 联动红线）",
        )
    if report is None or report.status != STATUS_PASSED:
        status = report.status if report is not None else "MISSING"
        return PublishDecision(
            allowed=False,
            code=SwapQualityFailed.code,
            reason=f"质量报告状态 {status}，未达 PASSED，禁止原子发布（FR-43/44）",
        )
    if await current_publish_state(session, run.id) == STATE_PUBLISHED:
        return PublishDecision(allowed=True, reason="幂等：该运行已发布")
    return PublishDecision(allowed=True, reason="四道门禁通过")


async def publish_run(
    session: AsyncSession,
    run: ExecutionRun,
    *,
    report: QualityReport | None,
    targets: PublishTargets,
    executor: SwapExecutor,
) -> PublishOutcome:
    """执行发布（门禁 → 幂等检查 → 原子 Swap → 事件留痕）。

    幂等：已发布过则直接返回成功，不再 swap（同一运行重复触发不重复发布）。
    被阻断（隔离 / 质量未过）不抛异常——「不发布」是合法结果，由调用方决定运行终态。

    Raises:
        SwapSchemaMismatch:     Schema 不一致（swap 前拦截）。
        DependencyUnavailable:  目标端 Doris 不可达。
    """
    decision = await evaluate_publish_gate(
        session, run, report=report, targets=targets
    )
    if not decision.allowed:
        await record_event(
            session,
            run.id,
            level="WARN",
            event_type=EVENT_BLOCKED,
            message=f"发布被阻断：{decision.reason}",
            stage="finalize",
        )
        return PublishOutcome(
            published=False,
            blocked=True,
            state=STATE_BLOCKED,
            reason=decision.reason,
            code=decision.code,
        )
    if await current_publish_state(session, run.id) == STATE_PUBLISHED:
        return PublishOutcome(
            published=True,
            blocked=False,
            state=STATE_PUBLISHED,
            reason="幂等：该运行已发布，不重复 Swap",
        )
    try:
        result: SwapResult = await execute_atomic_swap(
            executor,
            target_table=targets.target_table,
            shadow_table=targets.shadow_table,
        )
    except Exception as exc:
        # 失败留痕后原样抛出：调用方（_finalize_run）据此把运行置 FAILED 并触发诊断
        await record_event(
            session,
            run.id,
            level="ERROR",
            event_type=EVENT_FAILED,
            message=f"原子发布失败：{type(exc).__name__}",
            stage="finalize",
        )
        raise
    await record_event(
        session,
        run.id,
        level="INFO",
        event_type=EVENT_PUBLISHED,
        message=(
            f"原子发布完成：{result.target_table} ← {result.shadow_table}（swap=true，FR-43）"
        ),
        stage="finalize",
    )
    return PublishOutcome(
        published=True,
        blocked=False,
        state=STATE_PUBLISHED,
        reason="原子 Swap 成功，正式表已发布",
        executed_sql=result.executed_sql,
    )


__all__ = [
    "ACTION_QUARANTINE",
    "EVENT_BLOCKED",
    "EVENT_FAILED",
    "EVENT_PUBLISHED",
    "STATE_BLOCKED",
    "STATE_PUBLISHED",
    "STATE_READY",
    "STATE_SHADOW_WRITTEN",
    "PublishDecision",
    "PublishOutcome",
    "PublishTargets",
    "current_publish_state",
    "evaluate_publish_gate",
    "is_quarantined",
    "publish_run",
    "resolve_targets",
]
