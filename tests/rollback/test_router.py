"""API-13 路由用例（T-16.19 / FR-48，AC-08/AC-12）。

覆盖：申请（201，需项目成员）、审批（治理层裁决）、查询、清理扫描、
跨项目 404/403 隔离、响应零明文（AC-02）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import ExecutionRun
from etl_agent.rollback.artifacts import build_manifest
from tests.helpers import SeededWorkspace, make_client

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


async def _rollback_payload(run_id: uuid.UUID) -> dict:
    return {
        "reason": "引擎失败需受管回滚",
        "target_table": "orders_wide",
        "shadow_table": "orders_wide_shadow",
    }


async def test_router_create_rollback(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """API-13 申请：项目成员 → 201，申请为 PENDING，制品为 ROLLBACK_PENDING。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            json=await _rollback_payload(run.id),
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["artifact_status"] == "ROLLBACK_PENDING"
    assert body["retention_days"] == 7
    assert body["capacity_warn_threshold"] == 20


async def test_router_create_rollback_requires_member(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """非项目成员申请 → 403（AC-12）。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            json={"reason": "越权"},
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403


async def test_router_create_rollback_cross_project_404(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """运行不属于路径项目 → 404（不泄露存在性）。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_b_id}/executions/{run.id}/rollbacks",
            json={"reason": "跨项目"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 404


async def test_router_create_rollback_duplicate_409(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """同一运行重复申请 → 409。"""
    run = await make_run(status="FAILED")
    payload = {"reason": "首次"}
    async with make_client(app) as client:
        first = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            json=payload,
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        second = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            json=payload,
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "BIZ_409_ROLLBACK_STATE_CONFLICT"


async def test_router_rollback_requires_approval(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """审批端点在项目内成员可裁决；审批后状态 APPROVED（治理层）。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            json={"reason": "审批用例"},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        rollback_id = created.json()["id"]
        decided = await client.post(
            f"/api/v1/rollbacks/{rollback_id}/decisions",
            json={"decision": "APPROVE", "reason": "同意"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "APPROVED"
    assert decided.json()["decision"] == "APPROVE"


async def test_router_query_rollback_cross_project_403(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """跨项目查询回滚申请 → 403（非成员）。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            json={"reason": "查询用例"},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        rollback_id = created.json()["id"]
        forbidden = await client.get(
            f"/api/v1/rollbacks/{rollback_id}",
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert forbidden.status_code == 403


async def test_router_query_rollback_ok(app: FastAPI, seeded: SeededWorkspace, make_run) -> None:
    """项目成员查询回滚申请 → 200。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            json={"reason": "查询OK"},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        rollback_id = created.json()["id"]
        response = await client.get(
            f"/api/v1/rollbacks/{rollback_id}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200
    assert response.json()["id"] == rollback_id


async def test_router_cleanup_scan(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace, make_run
) -> None:
    """到期清理扫描端点：返回保留期摘要与计数（不触发数据面）。"""
    run = await make_run(status="FAILED")
    # 直接落一条已到期、已回滚的申请（绕过 API 以注入 created_at）
    from etl_agent.rollback.service import CreateRollbackInput, create_rollback_request

    manifest = build_manifest(
        run_id=run.id,
        target_table="orders_wide",
        shadow_table="orders_wide_shadow",
        error_table=None,
        retention_days=7,
        capacity_warn_threshold=20,
        now=NOW - timedelta(days=30),
    )
    request = await create_rollback_request(
        db_session,
        payload=CreateRollbackInput(
            run_id=run.id, requested_by=seeded.engineer_a_user_id, reason="到期"
        ),
        manifest=manifest,
        now=NOW - timedelta(days=30),
    )
    request.artifact_status = "ROLLED_BACK"
    await db_session.flush()
    await db_session.commit()

    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/rollbacks/cleanup-scan",
            json={},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "retention_summary" in body
    assert body["evaluated"] >= 0


async def test_router_rollback_response_zero_plaintext(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """AC-02：回滚响应零明文（无口令 / 无连接串 / 无占位符）。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            json={
                "reason": "零明文用例",
                "target_table": "orders_wide",
                "shadow_table": "orders_wide_shadow",
            },
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 201
    text = response.text.lower()
    assert "password" not in text
    assert "${" not in text
    assert "jdbc://" not in text


async def test_router_rollback_view_has_no_free_text_body() -> None:
    """出参模型字段集合固定：无 HOCON / Prompt / 正文类自由文本位。"""
    from etl_agent.rollback.schemas import RollbackCleanupView, RollbackResponse

    forbidden = {"hocon", "plan", "prompt", "content", "body", "connection_string"}
    for model in (RollbackResponse, RollbackCleanupView):
        fields = {name.lower() for name in model.model_fields}
        assert not (fields & forbidden), model.__name__


_ = ExecutionRun
