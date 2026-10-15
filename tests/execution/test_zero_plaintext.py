"""AC-02 专项：执行链路响应零明文（FR-67/68 红线）。

覆盖：API-11/12/14 响应体、执行事件流消息、质量报告诊断都不含连接口令与
HOCON 正文；出参模型字段集合固定（无自由文本正文位）。
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution import service as execution_service
from etl_agent.execution.routing import QualityOutcome, generate_quality_report
from etl_agent.infrastructure.models import ExecutionRun
from tests.helpers import (
    RecordingDispatcher,
    SeededExecution,
    SeededWorkspace,
    dev_pg_credentials,
    make_client,
)

CONTRACT = {"valid_sink": "orders_wide_shadow", "error_sink": "orders_wide_error"}


async def test_zero_plaintext_execution_responses(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_execution: SeededExecution,
) -> None:
    """AC-02：触发 / 查询 / 质量报告三类响应均无连接口令。"""
    app.state.execution_dispatcher = RecordingDispatcher()
    password = str(dev_pg_credentials()["password"])
    assert password

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    await execution_service.transition(db_session, run, "SUBMITTED", reason="Worker 已领取")
    await generate_quality_report(
        db_session, run.id, CONTRACT, QualityOutcome(total_rows=4, passed_rows=4)
    )

    headers = {"X-Actor-ID": SeededWorkspace.ADMIN_B}
    async with make_client(app) as client:
        triggered = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            json={"preparation_id": str(seeded_execution.preparation_id)},
            headers=headers,
        )
        detail = await client.get(
            f"/api/v1/executions/{seeded_execution.run_id}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        report = await client.get(
            f"/api/v1/executions/{seeded_execution.run_id}/quality-report",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert triggered.status_code == 202
    assert detail.status_code == 200
    assert report.status_code == 200

    for response in (triggered, detail, report):
        text = response.text
        assert password not in text
        assert "password" not in text.lower()
        assert "${" not in text  # 连占位符形态都不该出现在执行响应里


async def test_execution_views_have_no_free_text_body() -> None:
    """出参模型字段集合固定：无「正文 / HOCON / Prompt」类自由文本位（FR-67 三层隔离）。"""
    from etl_agent.execution.schemas import (
        ExecutionDetailResponse,
        ExecutionRunView,
        ExecutionTriggerResponse,
    )

    forbidden = {"hocon", "plan", "etl_plan", "prompt", "candidate", "content", "body"}
    for model in (ExecutionRunView, ExecutionDetailResponse, ExecutionTriggerResponse):
        fields = {name.lower() for name in model.model_fields}
        assert not (fields & forbidden), model.__name__


async def test_event_messages_carry_no_plaintext(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """事件流消息只含治理事实（状态 / 原因 / 表名），不含凭据或 HOCON 正文。"""
    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    await execution_service.transition(
        db_session, run, "SUBMITTED", reason="Worker 已领取受管执行命令"
    )
    from sqlalchemy import select

    from etl_agent.infrastructure.models import ExecutionEvent

    events = (await db_session.execute(select(ExecutionEvent))).scalars().all()
    assert events
    payload = json.dumps([item.message for item in events], ensure_ascii=False)
    assert "password" not in payload.lower()
    assert "${" not in payload
