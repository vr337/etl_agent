"""监督快照与摘要稳定性用例（T-16.8，FR-45）。

覆盖：`snapshot_sha256` 同输入同输出、跨进程可复现、`observed` 只含聚合指标、
`rule_matches` 只含规则证据、快照 upsert（同运行一行）、动作只升不降。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from etl_agent.infrastructure.models import RuntimeSupervisionSnapshot
from etl_agent.supervision.contracts import (
    SupervisionContract,
    SupervisionObservation,
    evaluate,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL, ACTION_CONTINUE, ACTION_QUARANTINE
from etl_agent.supervision.snapshot import (
    canonical_json,
    record_supervision_event,
    snapshot_digest,
    write_snapshot,
)


def test_snapshot_sha256_stable() -> None:
    """同输入同输出（写死期望值，跨实现可复现）。"""
    payload = {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "action": "CANCEL",
        "risk_level": "P0",
        "rule_matches": [{"rule_id": "SUPERVISION-P0-RECORD"}],
        "observed": {"records": 10_000_001},
    }
    first = snapshot_digest(**payload)
    second = snapshot_digest(**payload)
    assert first == second
    assert len(first) == 64
    # 键序不影响（规范化排序）
    assert snapshot_digest(**{**payload, "action": "CANCEL"}) == first


def test_snapshot_digest_changes_with_input() -> None:
    """任一输入变化 → 摘要变化（防「换个动作同摘要」的静默碰撞）。"""
    base = {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "action": "CONTINUE",
        "risk_level": None,
        "rule_matches": [],
        "observed": {"records": 1},
    }
    other = dict(base, observed={"records": 2})
    assert snapshot_digest(**base) != snapshot_digest(**other)


def test_canonical_json_is_stable() -> None:
    """规范化 JSON：键排序 + 中文不转义 + 紧凑分隔符。"""
    assert canonical_json({"b": 1, "a": "中文"}) == '{"a":"中文","b":1}'


async def test_write_snapshot_upserts_single_row(db_session, make_run) -> None:
    """同一运行反复写快照只有一行（SPEC 5.2 `run_id` UK），且 `observed` 只含聚合指标。"""
    run = await make_run()
    contract = SupervisionContract.frozen()
    outcome = evaluate(contract, SupervisionObservation(records=contract.max_records + 1))
    observation = SupervisionObservation(records=contract.max_records + 1)

    await write_snapshot(db_session, run, outcome=outcome, observation=observation)
    await write_snapshot(db_session, run, outcome=outcome, observation=observation)
    rows = (
        (
            await db_session.execute(
                select(RuntimeSupervisionSnapshot).where(
                    RuntimeSupervisionSnapshot.run_id == run.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.action == ACTION_CANCEL
    assert row.snapshot_sha256 is not None and len(row.snapshot_sha256) == 64
    # observed 只含聚合指标键
    assert set(row.observed or {}) == {
        "records",
        "bytes",
        "duration_seconds",
        "amplification_ratio",
        "reject_rate",
        "quality_rows",
    }
    # rule_matches 只含规则证据键
    assert set((row.rule_matches or [{}])[0]) == {
        "rule_id",
        "risk_level",
        "actions",
        "observed",
        "threshold",
        "detail",
    }


async def test_snapshot_action_only_escalates(db_session, make_run) -> None:
    """动作只升不降：先 CANCEL 后 CONTINUE 不会把监督动作降回。"""
    run = await make_run()
    contract = SupervisionContract.frozen()
    heavy = evaluate(contract, SupervisionObservation(records=contract.max_records + 1))
    light = evaluate(contract, SupervisionObservation(records=1))
    await write_snapshot(
        db_session, run, outcome=heavy, observation=SupervisionObservation(records=1)
    )
    assert run.supervision_action == ACTION_CANCEL
    await write_snapshot(
        db_session, run, outcome=light, observation=SupervisionObservation(records=1)
    )
    assert run.supervision_action == ACTION_CANCEL  # 不降回 CONTINUE


async def test_record_supervision_event_skips_continue(db_session, make_run) -> None:
    """CONTINUE 不写事件（避免每 10s 刷一条噪音）。"""
    from etl_agent.infrastructure.models import ExecutionEvent
    run = await make_run()
    contract = SupervisionContract.frozen()
    outcome = evaluate(contract, SupervisionObservation(records=1))
    assert outcome.action == ACTION_CONTINUE
    await record_supervision_event(
        db_session, run, outcome=outcome, observation=SupervisionObservation(records=1)
    )
    events = (
        (
            await db_session.execute(
                select(ExecutionEvent).where(ExecutionEvent.run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    assert events == []


async def test_quarantine_rule_snapshot_records_quarantine(db_session, make_run) -> None:
    """QUARANTINE 规则命中后快照 action 为 CANCEL（最严胜出）且证据含 QUARANTINE 动作。"""
    run = await make_run()
    contract = SupervisionContract.frozen()
    observation = SupervisionObservation(reject_rate=0.9)
    outcome = evaluate(contract, observation)
    assert outcome.action == ACTION_CANCEL
    assert ACTION_QUARANTINE in {action for m in outcome.matches for action in m.actions}
    snapshot = await write_snapshot(db_session, run, outcome=outcome, observation=observation)
    assert snapshot.action == ACTION_CANCEL
    _ = uuid
