"""M-L 运行时监督 — QUARANTINE 隔离（阻断发布与原子 Swap，FR-46）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 execution_runs.quarantined_at /
第 8 章「安全 · 数据面发布」
覆盖 FR：FR-46（QUARANTINE 阻断后续质量发布与原子交换）、FR-43（发布门禁）
对应 AC：AC-08（隔离生效）、AC-07（隔离后不得发布）

**隔离是单向事实**：`quarantined_at` 一旦写入**不会被监督链路自动清除**——
解除隔离是人工裁决（复盘后确认数据可信），不是指标回落就能自动恢复的状态。
本模块只提供「加隔离」与「查隔离」，不提供自动解除（避免误把「数据不可信」当成临时状态）。

阻断点（`execution/publish.py` 的第 1 道门禁）：
`quarantined_at IS NOT NULL` 或 `supervision_action == QUARANTINE` → 原子 Swap 被拒
（`BIZ_422_SWAP_QUARANTINED`）。这是 S8 与 T-15 的联动红线（禁止事项 15）。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.service import record_event
from etl_agent.infrastructure.models import ExecutionRun
from etl_agent.supervision.mitigation import ACTION_QUARANTINE


async def quarantine_run(
    session: AsyncSession, run: ExecutionRun, *, reason: str
) -> ExecutionRun:
    """给运行加隔离（幂等：已隔离则只更新时间戳不动语义）。

    幂等语义：重复隔离不重复写事件（避免每轮监督刷一条），但仍刷新 `quarantined_at`
    以表达「隔离依据在持续成立」。
    """
    already = bool(run.quarantined_at)
    run.quarantined_at = datetime.now(UTC)
    run.supervision_action = ACTION_QUARANTINE
    await session.flush()
    if not already:
        await record_event(
            session,
            run.id,
            level="ERROR",
            event_type="execution.quarantined",
            message=f"运行已隔离（阻断发布与原子 Swap）：{_brief(reason)}",
            stage="supervise",
        )
    return run


def is_quarantined(run: ExecutionRun) -> bool:
    """是否处于隔离态（时间戳或监督动作任一命中）。"""
    return bool(run.quarantined_at) or run.supervision_action == ACTION_QUARANTINE


def _brief(text: str, limit: int = 200) -> str:
    return " ".join(text.split())[:limit]


__all__ = ["is_quarantined", "quarantine_run"]
