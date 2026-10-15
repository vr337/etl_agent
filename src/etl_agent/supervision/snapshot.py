"""M-L 运行时监督 — 监督快照（FR-45，`snapshot_sha256`）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 runtime_supervision_snapshots /
第 8 章「可观测」
覆盖 FR：FR-45（快照带 `snapshot_sha256`）、FR-39（运行中心可查看监督快照）
对应 AC：AC-08（失败快照可追溯）、AC-02（快照不含日志正文与原始数据）

**快照只装三类东西**（PRD 10.5 / SPEC 8「可观测」）：
- `action` / `risk_level`：监督判决；
- `rule_matches`：**规则证据**（规则 ID / 风险 / 动作 / 聚合实测值 / 阈值）；
- `observed`：**聚合指标**（records / bytes / duration / amplification / reject_rate）。

不含日志正文、不含原始数据行、不含错误栈全文。

**`snapshot_sha256` 的稳定性**：对规范化 JSON（`sort_keys=True`、`ensure_ascii=False`、
紧凑分隔符）取 SHA256——同一输入必然同一输出，跨进程 / 跨机器可复现。
周期快照对同一运行是 **upsert**（SPEC 5.2 的 `run_id` UK）：一行承载最新判决，
历史轨迹由 `execution_events` 承载。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.service import record_event
from etl_agent.infrastructure.models import ExecutionRun, RuntimeSupervisionSnapshot
from etl_agent.supervision.contracts import SupervisionObservation, SupervisionOutcome
from etl_agent.supervision.mitigation import (
    ACTION_CONTINUE,
    ACTION_QUARANTINE,
    stricter_of,
)


def canonical_json(payload: Any) -> str:
    """规范化 JSON（快照哈希的唯一序列化口径）。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def snapshot_digest(
    *,
    run_id: str,
    action: str,
    risk_level: str | None,
    rule_matches: list[dict[str, Any]],
    observed: dict[str, Any],
) -> str:
    """快照摘要（同输入同输出，FR-45）。"""
    return hashlib.sha256(
        canonical_json(
            {
                "run_id": run_id,
                "action": action,
                "risk_level": risk_level,
                "rule_matches": rule_matches,
                "observed": observed,
            }
        ).encode("utf-8")
    ).hexdigest()


async def write_snapshot(
    session: AsyncSession,
    run: ExecutionRun,
    *,
    outcome: SupervisionOutcome,
    observation: SupervisionObservation,
) -> RuntimeSupervisionSnapshot:
    """写入 / 更新监督快照并把动作回写运行（动作只升不降）。

    `ExecutionRun.supervision_action` 用 `stricter_of` 合并：**只收窄不放宽**——
    指标回落不会把 CANCEL/QUARANTINE 自动降回 CONTINUE（解除需人工裁决）。
    """
    rule_matches = outcome.evidence()
    observed = observation.as_observed()
    digest = snapshot_digest(
        run_id=str(run.id),
        action=outcome.action,
        risk_level=outcome.risk_level or None,
        rule_matches=rule_matches,
        observed=observed,
    )
    snapshot = (
        (
            await session.execute(
                select(RuntimeSupervisionSnapshot).where(
                    RuntimeSupervisionSnapshot.run_id == run.id
                )
            )
        )
        .scalars()
        .first()
    )
    if snapshot is None:
        snapshot = RuntimeSupervisionSnapshot(run_id=run.id)
        session.add(snapshot)
    snapshot.action = outcome.action
    snapshot.risk_level = outcome.risk_level or None
    snapshot.rule_matches = rule_matches
    snapshot.observed = observed
    snapshot.snapshot_sha256 = digest
    run.supervision_action = stricter_of(run.supervision_action or ACTION_CONTINUE, outcome.action)
    await session.flush()
    return snapshot


async def record_supervision_event(
    session: AsyncSession,
    run: ExecutionRun,
    *,
    outcome: SupervisionOutcome,
    observation: SupervisionObservation,
) -> None:
    """把监督判决写入事件流（运行中心的时间线来源）。

    命中规则记 WARN（CONTINUE 不写事件，避免每 10s 刷一条噪音）。
    """
    if outcome.action == ACTION_CONTINUE:
        return
    level = "ERROR" if outcome.action in {"CANCEL", ACTION_QUARANTINE} else "WARN"
    rule_ids = ", ".join(outcome.rule_ids) if outcome.rule_ids else "—"
    await record_event(
        session,
        run.id,
        level=level,
        event_type="execution.supervision",
        message=(
            f"监督动作 {outcome.action}（{outcome.risk_level or 'P3'}）：命中 {rule_ids}"
            f"（水位 {observation.as_observed()}）"
        ),
        stage="supervise",
    )


__all__ = [
    "canonical_json",
    "record_supervision_event",
    "snapshot_digest",
    "write_snapshot",
]
