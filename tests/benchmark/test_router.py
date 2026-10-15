"""S9 Benchmark — API-15 路由用例（仅 SECURITY_APPROVER + 跨项目 404 + 零明文）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-15 / 第 6.2 章接口契约 /
第 6.4 章错误码 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-50（运行 Benchmark）、FR-66（项目隔离）
对应 AC：AC-09（六项门禁）、AC-12（跨项目 404）、AC-02（响应零明文）
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import BenchmarkRun
from tests.helpers import SeededWorkspace, make_client


async def test_router_run_benchmark_requires_security_approver(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**AC-01/AC-12**：非 SECURITY_APPROVER 触发 Benchmark → 403。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/benchmark-runs",
            json={"suite_id": "harness-v2", "manifest_version": "v1", "layers": ["L0"]},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 403


async def test_router_run_benchmark_as_security_approver(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**AC-09**：SECURITY_APPROVER 触发 → 201，返回六项门禁摘要与 report_sha256。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/benchmark-runs",
            json={"suite_id": "harness-v2", "manifest_version": "v1", "layers": ["L0", "L1", "L2"]},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["suite_id"] == "harness-v2"
    assert body["status"] == "PASS"
    assert len(body["gates"]) == 6
    assert len(body["report_sha256"]) == 64
    assert body["manifest_sha256"]


async def test_router_run_benchmark_outsider_forbidden(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """非项目成员 → 403（AC-12）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/benchmark-runs",
            json={"suite_id": "harness-v2", "manifest_version": "v1", "layers": ["L0"]},
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403


async def test_router_list_benchmark_runs_member_readable(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """运行列表：项目成员可读（与触发收紧到 SECURITY_APPROVER 形成对比）。"""
    async with make_client(app) as client:
        await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/benchmark-runs",
            json={"suite_id": "harness-v2", "manifest_version": "v1", "layers": ["L0"]},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/benchmark-runs",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["runs"][0]["suite_id"] == "harness-v2"


async def test_router_get_run_detail(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """运行详情：返回摘要与六项指标值。"""
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/benchmark-runs",
            json={"suite_id": "harness-v2", "manifest_version": "v1", "layers": ["L0"]},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        run_id = created.json()["run_id"]
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/benchmark-runs/{run_id}",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == run_id


async def test_router_get_run_cross_project_404(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver, db_session: AsyncSession
) -> None:
    """跨项目查询运行 → 404（不泄露存在性）。"""
    run = BenchmarkRun(
        project_id=seeded.project_a_id,
        suite_id="harness-v2",
        manifest_version="v1",
        manifest_sha256="a" * 64,
        status="PASS",
        metrics={"p0_p1_block_rate": 1.0},
        report_sha256="b" * 64,
    )
    db_session.add(run)
    await db_session.flush()

    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_b_id}/benchmark-runs/{run.id}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 404


async def test_router_get_run_missing_404(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """不存在的运行 → 404。"""
    import uuid

    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/benchmark-runs/{uuid.uuid4()}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 404


async def test_router_response_has_no_plaintext(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**AC-02**：响应不含凭证明文（只回摘要与计数）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/benchmark-runs",
            json={"suite_id": "harness-v2", "manifest_version": "v1", "layers": ["L0"]},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    text = response.text.lower()
    for marker in ("password", "signature_value", "private_key", "-----begin"):
        assert marker not in text
