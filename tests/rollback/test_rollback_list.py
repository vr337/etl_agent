"""API-13 回滚列表端点用例（联调修复新增，fix/S10-e2e-integration）。

覆盖：GET /projects/{id}/executions/{rid}/rollbacks 的空列表 / 有数据
（前端契约口径：execution_run_id / risk_level）/ 分页 / 跨项目 404。
"""
from __future__ import annotations

from fastapi import FastAPI

from tests.helpers import SeededWorkspace, make_client


async def _create_rollback(client: object, project_id: str, run_id: object, reason: str):
    """经 API-13 真实创建回滚申请（列表用例的前置事实）。"""
    return await client.post(  # type: ignore[attr-defined]
        f"/api/v1/projects/{project_id}/executions/{run_id}/rollbacks",
        json={"reason": reason},
        headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
    )


async def test_list_rollbacks_empty(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """空列表：运行无回滚申请 → 200 + 空数组 + X-Total-Count=0。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


async def test_list_rollbacks_with_data(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """有数据：返回前端契约口径（execution_run_id / risk_level=null），只含治理事实。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        created = await _create_rollback(client, seeded.project_a_id, run.id, "引擎失败需回滚")
        assert created.status_code == 201, created.text
        rollback_id = created.json()["id"]

        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["X-Total-Count"] == "1"
    assert len(body) == 1
    item = body[0]
    assert item["id"] == rollback_id
    assert item["execution_run_id"] == str(run.id)
    assert item["status"] == "PENDING"
    # 后端 rollback_requests 无风险列（PDP 判决在 Harness 副作用准备单），如实回 None
    assert item["risk_level"] is None
    assert item["artifact_manifest"] is not None


async def test_list_rollbacks_pagination(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """分页：同一运行可有多条申请（前一条终态后），limit/offset 生效。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        first = await _create_rollback(client, seeded.project_a_id, run.id, "首次申请")
        assert first.status_code == 201, first.text
        first_id = first.json()["id"]
        # 治理层驳回 → REJECTED（终态，不再阻塞同一运行的新申请）
        decided = await client.post(
            f"/api/v1/rollbacks/{first_id}/decisions",
            json={"decision": "REJECT", "reason": "误报"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert decided.status_code == 200, decided.text
        second = await _create_rollback(client, seeded.project_a_id, run.id, "再次申请")
        assert second.status_code == 201, second.text

        path = f"/api/v1/projects/{seeded.project_a_id}/executions/{run.id}/rollbacks"
        first_page = await client.get(
            path, params={"limit": 1}, headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A}
        )
        second_page = await client.get(
            path, params={"limit": 1, "offset": 1}, headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A}
        )
    assert first_page.status_code == 200, first_page.text
    assert first_page.headers["X-Total-Count"] == "2"
    assert len(first_page.json()) == 1
    assert second_page.status_code == 200, second_page.text
    assert second_page.headers["X-Total-Count"] == "2"
    assert len(second_page.json()) == 1


async def test_list_rollbacks_cross_project_404(
    app: FastAPI, seeded: SeededWorkspace, make_run
) -> None:
    """运行不属于路径项目 → 404（不泄露存在性，AC-12）。"""
    run = await make_run(status="FAILED")
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_b_id}/executions/{run.id}/rollbacks",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 404
