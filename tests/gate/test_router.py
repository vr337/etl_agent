"""门禁 / 冻结路由测试（T-10；S5 提示词第十二节 tests/gate/test_router）。

覆盖：未通过门禁的版本冻结 → 409；跨项目冻结 → 403；
门禁失败摘要不含候选正文（AC-02）。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.gate.service import build_repair_feedback, run_gate
from etl_agent.infrastructure.models import AgentRun
from tests.gate.test_rules import COMPLIANT_HOCON, base_context, base_plan
from tests.helpers import SeededWorkspace, make_client


async def test_router_freeze_not_validated_409(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline_version: uuid.UUID
) -> None:
    """版本未通过门禁（无 SHA256）→ 409 BIZ_409_STATE_CONFLICT。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/pipeline-versions/{seeded_pipeline_version}/freeze",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "BIZ_409_STATE_CONFLICT"


async def test_router_freeze_unknown_version_404(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """未知版本 → 404 BIZ_404_NOT_FOUND（SPEC 6.4 通用码）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/pipeline-versions/{uuid.uuid4()}/freeze",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_NOT_FOUND"


async def test_router_freeze_cross_project_403(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_profiles: dict[str, uuid.UUID],
) -> None:
    """局外人对他人版本冻结 → 403 AUTH_403_NOT_PROJECT_MEMBER（AC-12）。"""
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json={
                "requirement": "生成后冻结",
                "source_profile_ids": [str(seeded_profiles["source"])],
                "sink_profile_ids": [str(seeded_profiles["sink"])],
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert created.status_code == 201
        run = (
            await db_session.execute(
                select(AgentRun).where(AgentRun.id == uuid.UUID(created.json()["id"]))
            )
        ).scalar_one()
        response = await client.post(
            f"/api/v1/pipeline-versions/{run.pipeline_version_id}/freeze",
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"


async def test_gate_failure_summary_has_no_candidate_body() -> None:
    """AC-02：门禁失败摘要只含规则 ID + 脱敏摘要，不含 HOCON 正文与明文凭据。"""
    plan = base_plan()
    plan["source"]["connector_type"] = "MONGODB"
    plan["sink"]["password"] = "s3cr3t-value"
    result = await run_gate(plan, COMPLIANT_HOCON, base_context())
    assert not result.passed

    feedback = build_repair_feedback(result)
    assert "GATE-002" in feedback
    assert "GATE-001" in feedback
    # 摘要与序列化结果都不含凭据值 / HOCON 正文（只允许出现字段名）
    assert "s3cr3t-value" not in feedback
    assert "s3cr3t-value" not in str(result.to_dict())
    assert "job.mode" not in feedback
    assert "值已省略" in feedback
