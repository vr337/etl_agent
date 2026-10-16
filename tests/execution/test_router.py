"""API-11/12/14 路由用例（T-13 公共产出 / FR-38/39/44）。

覆盖：API-11 触发受管执行（Operator 校验 + 即时派发 + 幂等）、API-12 状态与事件流、
API-14 质量报告、跨项目 403 / 404 隔离。
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.routing import QualityOutcome, generate_quality_report
from etl_agent.infrastructure.models import ExecutionRun
from tests.execution.conftest import RunFactory
from tests.helpers import RecordingDispatcher, SeededExecution, SeededWorkspace, make_client

CONTRACT = {"valid_sink": "orders_wide_shadow", "error_sink": "orders_wide_error"}


async def test_router_trigger_execution(
    app: FastAPI, seeded: SeededWorkspace, seeded_execution: SeededExecution
) -> None:
    """API-11：Operator 触发已 Commit 的准备单 → 202 + 即时派发一次扫描。"""
    dispatcher = RecordingDispatcher()
    app.state.execution_dispatcher = dispatcher

    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            json={"preparation_id": str(seeded_execution.preparation_id), "trigger": "MANUAL"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["dispatched"] is True
    assert body["run"]["id"] == str(seeded_execution.run_id)
    assert body["run"]["status"] == "QUEUED"
    assert body["run"]["trigger"] == "MANUAL"
    assert dispatcher.calls == 1


async def test_router_trigger_requires_operator(
    app: FastAPI, seeded: SeededWorkspace, seeded_execution: SeededExecution
) -> None:
    """API-11 仅 OPERATOR 可触发（AUTH_403_ROLE_REQUIRED；ADMIN 不隐式升级）。"""
    app.state.execution_dispatcher = RecordingDispatcher()
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            json={"preparation_id": str(seeded_execution.preparation_id)},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_ROLE_REQUIRED"


async def test_router_trigger_dispatched_false_when_broker_down(
    app: FastAPI, seeded: SeededWorkspace, seeded_execution: SeededExecution
) -> None:
    """派发失败不阻塞响应（Beat 周期兜底，FR-35 至少一次投递）。"""
    app.state.execution_dispatcher = RecordingDispatcher(fail=True)
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            json={"preparation_id": str(seeded_execution.preparation_id)},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 202
    assert response.json()["dispatched"] is False


async def test_router_trigger_rejects_terminal_run(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_execution: SeededExecution,
) -> None:
    """终态运行不可重复触发（重试须新建运行，FR-42）。"""
    app.state.execution_dispatcher = RecordingDispatcher()
    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    run.status = "SUCCEEDED"
    await db_session.flush()

    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            json={"preparation_id": str(seeded_execution.preparation_id)},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "BIZ_409_EXECUTION_STATE_CONFLICT"


async def test_router_trigger_rejects_version_mismatch(
    app: FastAPI, seeded: SeededWorkspace, seeded_execution: SeededExecution
) -> None:
    """声明版本与准备单登记版本不一致 → 409（防串版本触发）。"""
    app.state.execution_dispatcher = RecordingDispatcher()
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            json={
                "preparation_id": str(seeded_execution.preparation_id),
                "pipeline_version_id": str(uuid.uuid4()),
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 409


async def test_router_trigger_rejects_invalid_trigger_value(
    app: FastAPI, seeded: SeededWorkspace, seeded_execution: SeededExecution
) -> None:
    """trigger 取值非法 → BIZ_400_VALIDATION。"""
    app.state.execution_dispatcher = RecordingDispatcher()
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            json={"preparation_id": str(seeded_execution.preparation_id), "trigger": "CRON"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BIZ_400_VALIDATION"


async def test_router_trigger_unknown_preparation_is_404(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """不存在的准备单 → 404（不泄露存在性）。"""
    app.state.execution_dispatcher = RecordingDispatcher()
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            json={"preparation_id": str(uuid.uuid4())},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_EXECUTION_NOT_FOUND"


async def test_router_query_execution(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_execution: SeededExecution,
) -> None:
    """API-12：返回状态 + 事件流 + 行数 / 字节 + 监督快照（S7 恒 None）。"""
    from etl_agent.execution import service as execution_service

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    await execution_service.transition(db_session, run, "SUBMITTED", reason="Worker 已领取")

    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/executions/{seeded_execution.run_id}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUBMITTED"
    assert body["supervision_snapshot"] is None
    assert [item["event_type"] for item in body["events"]] == ["execution.submitted"]
    assert body["source_rows"] is None and body["sink_bytes"] is None


async def test_router_query_execution_cross_project_denied(
    app: FastAPI, seeded_execution: SeededExecution
) -> None:
    """跨项目访问 → 403（AC-12：非成员不区分「不存在」与「无权限」）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/executions/{seeded_execution.run_id}",
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"


async def test_router_quality_report(
    app: FastAPI, db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """API-14：质量报告就绪后返回聚合事实（未生成时 404）。"""
    async with make_client(app) as client:
        missing = await client.get(
            f"/api/v1/executions/{seeded_execution.run_id}/quality-report",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert missing.status_code == 404

    await generate_quality_report(
        db_session,
        seeded_execution.run_id,
        CONTRACT,
        QualityOutcome(
            total_rows=1000, passed_rows=999, rejected_rows=1, error_codes={"NULL_ID": 1}
        ),
    )
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/executions/{seeded_execution.run_id}/quality-report",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == str(seeded_execution.run_id)
    assert body["status"] == "PASSED"
    assert body["total_rows"] == 1000 and body["rejected_rows"] == 1
    assert body["valid_sink"] == "orders_wide_shadow"


# ============================================================
# API-12 列表（联调修复新增）：GET /projects/{id}/executions
# ============================================================


async def test_router_list_executions_empty(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """空列表：项目下无运行 → 200 + 空数组 + X-Total-Count=0（不报 404）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


async def test_router_list_executions_with_data(
    app: FastAPI,
    seeded: SeededWorkspace,
    make_run: RunFactory,
) -> None:
    """有数据：返回前端展示口径字段（trigger_mode / target_rows / pipeline_name）。"""
    run = await make_run(status="SUCCEEDED", trigger="MANUAL")

    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["X-Total-Count"] == "1"
    assert len(body) == 1
    item = body[0]
    assert item["id"] == str(run.id)
    assert item["status"] == "SUCCEEDED"
    assert item["trigger_mode"] == "MANUAL"
    assert item["retry_count"] == 0
    assert item["target_rows"] is None  # sink_rows 未填
    assert "pipeline_name" in item and "supervision_action" in item


async def test_router_list_executions_pagination_and_filter(
    app: FastAPI,
    seeded: SeededWorkspace,
    make_run: RunFactory,
) -> None:
    """分页 + 状态过滤：limit/offset 生效，X-Total-Count 反映过滤后总数。"""
    await make_run(status="SUCCEEDED")
    await make_run(status="SUCCEEDED")
    await make_run(status="FAILED")

    async with make_client(app) as client:
        filtered = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            params={"status": "SUCCEEDED"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        paged = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            params={"limit": 1, "offset": 1},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert filtered.status_code == 200, filtered.text
    assert filtered.headers["X-Total-Count"] == "2"
    assert len(filtered.json()) == 2

    assert paged.status_code == 200, paged.text
    assert paged.headers["X-Total-Count"] == "3"  # 全量总数，不受分页影响
    assert len(paged.json()) == 1  # limit=1 只回一条


async def test_router_list_executions_invalid_status_rejected(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """非法 status → BIZ_400_VALIDATION（不做静默全量返回）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/executions",
            params={"status": "NOT_A_STATUS"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BIZ_400_VALIDATION"
