"""F3 审计钩子接线回归（钩子 5~6：执行状态迁移 / 监督动作）。

背景：同 tests/harness/test_audit_hooks.py——S9 的 on_execution_transition 与
on_supervision_action 曾在 src/ 零调用点，证据账本恒空。本组守 worker 侧接线：
- `execution/service.py::transition`（worker/API 共用咽喉）每次合法迁移双写
  EXECUTION_TRANSITION（project_id 经派生链查询，孤儿运行仅告警不抛——
  人类裁决补充 2：钩子失败不阻断业务迁移）；
- `worker/tasks/supervise.py::supervise_run` 每次监督判罚双写 SUPERVISION_ACTION。

提交语义：worker 会话按命令/周期 commit，钩子只 flush；监督用例为服务级直接
调用（周期任务另有 commit 外壳），断言 flush 后同会话可见即证明接线。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import HarnessEvidenceEvent
from etl_agent.supervision.contracts import SupervisionContract
from etl_agent.worker.tasks.consume_outbox import scan_outbox_once
from etl_agent.worker.tasks.supervise import supervise_run
from tests.helpers import FakeSeaTunnelClient, SeededExecution, make_settings
from tests.worker.test_consume_outbox import _loader


async def _evidence_event_types(db_session: AsyncSession) -> set[str]:
    rows = (
        (await db_session.execute(select(HarnessEvidenceEvent.event_type))).scalars().all()
    )
    return set(rows)


async def test_execution_transitions_emit_evidence(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """钩子 5：happy path 的每次状态迁移都落 EXECUTION_TRANSITION 证据行。"""
    engine = FakeSeaTunnelClient(statuses=["FINISHED"])
    summary = await scan_outbox_once(
        db_session,
        handoff=seeded_execution.handoff,
        engine=engine,
        settings=make_settings(),
        candidate_loader=_loader(seeded_execution),
        worker_id="worker-hook-test",
    )
    assert summary.submitted == 1

    types = await _evidence_event_types(db_session)
    assert "EXECUTION_TRANSITION" in types


async def test_supervision_action_emits_evidence(
    db_session: AsyncSession, make_run
) -> None:
    """钩子 6：监督判罚落 SUPERVISION_ACTION 证据行（与快照同会话）。"""
    run = await make_run(status="RUNNING", source_rows=10, sink_rows=11)
    action = await supervise_run(
        db_session, run, contract=SupervisionContract.frozen(), report=None
    )
    await db_session.flush()
    assert action  # CONTINUE / 减损动作之一均可，本用例只守证据双写

    types = await _evidence_event_types(db_session)
    assert "SUPERVISION_ACTION" in types
