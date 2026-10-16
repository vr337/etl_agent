"""F3 审计钩子接线回归（钩子 7：回滚申请 / 审批 / 执行三相）。

背景：同 tests/harness/test_audit_hooks.py——S9 的 on_rollback_event 曾在 src/
零调用点。本组守 rollback 域三相接线：REQUESTED（create_rollback_request）/
DECIDED（decide_rollback）经路由真实 commit；EXECUTED（mark_executed）为服务级
调用——src 中 mark_executing/mark_executed 暂无 worker 驱动（S8 遗留，接线在
相位函数内，未来执行任务接入即自动留痕），断言 flush 后同会话可见。

注意：钩子 7 的 phase 入参是 REQUESTED / DECIDED / EXECUTED 三个字面量，
非法 phase 由钩子自身 raise ValueError（既有契约，不重复测）。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import HarnessEvidenceEvent, HarnessEvidenceStream
from etl_agent.rollback.service import mark_executed, mark_executing, run_project
from tests.helpers import SeededWorkspace, make_client


async def _evidence_event_types(db_session: AsyncSession, project_id) -> set[str]:
    """项目下证据事件类型集合（按项目过滤：流按项目唯一，全表查询会被
    历史工作区污染——联调 dev 库常态）。"""
    rows = (
        (
            await db_session.execute(
                select(HarnessEvidenceEvent.event_type)
                .join(
                    HarnessEvidenceStream,
                    HarnessEvidenceStream.id == HarnessEvidenceEvent.stream_id,
                )
                .where(HarnessEvidenceStream.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def test_rollback_phases_emit_evidence(
    app, db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """钩子 7：回滚三相全部双写证据账本（REQUESTED / DECIDED / EXECUTED）。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            json={"reason": "钩子接线用例"},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        assert created.status_code == 201, created.text
        rollback_id = created.json()["id"]
        decided = await client.post(
            f"/api/v1/rollbacks/{rollback_id}/decisions",
            json={"decision": "APPROVE", "reason": "同意"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert decided.status_code == 200, decided.text

    # EXECUTED 相位：服务级推进（S8 起无 worker 驱动，接线在 mark_executed 内）
    await mark_executing(db_session, __import__("uuid").UUID(rollback_id))
    await mark_executed(db_session, __import__("uuid").UUID(rollback_id))
    await db_session.flush()

    types = await _evidence_event_types(db_session, await run_project(db_session, run.id))
    assert "ROLLBACK_REQUESTED" in types
    assert "ROLLBACK_DECIDED" in types
    assert "ROLLBACK_EXECUTED" in types
