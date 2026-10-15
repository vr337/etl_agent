"""生成状态机流转测试（T-09；S5 提示词第十二节 tests/generation）。

覆盖：参数齐备 → 生成 → 门禁通过 → 等审批（并冻结 SHA256）；参数缺失 → WAITING_INPUT。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import AgentRun, PipelineArtifact, PipelineVersion
from tests.helpers import SeededWorkspace, make_client


def run_payload(source_id: uuid.UUID, sink_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    """API-05 请求体（S5 提示词 11.9）。"""
    payload: dict[str, object] = {
        "requirement": "把 orders 汇总成宽表 orders_wide，按 id 去重",
        "source_profile_ids": [str(source_id)],
        "sink_profile_ids": [str(sink_id)],
    }
    payload.update(overrides)
    return payload


async def test_state_machine_happy_path(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_profiles: dict[str, uuid.UUID],
    seeded_pipeline_version: uuid.UUID,
) -> None:
    """参数齐备 → 生成 → 门禁通过 → WAITING_APPROVAL + 版本冻结 SHA256（FR-16/19/26）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json=run_payload(
                seeded_profiles["source"],
                seeded_profiles["sink"],
                pipeline_version_id=str(seeded_pipeline_version),
            ),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "WAITING_APPROVAL"
    assert body["pending_question"] is None
    assert body["thread_id"]
    # 遥测：1 份候选、0 次修复（FR-17）
    assert body["generation_candidate_count"] == 1
    assert body["generation_repair_count"] == 0

    version = await db_session.get(PipelineVersion, seeded_pipeline_version)
    assert version is not None
    assert version.status == "VALIDATED"
    assert version.artifact_sha256 is not None and len(version.artifact_sha256) == 64

    artifacts = (
        (
            await db_session.execute(
                select(PipelineArtifact).where(
                    PipelineArtifact.pipeline_version_id == version.id
                )
            )
        )
        .scalars()
        .all()
    )
    # 制品只存摘要，不存正文（FR-67）；三类制品与 SPEC 5.2 artifact_type 一致
    assert {item.artifact_type for item in artifacts} == {"plan", "hocon", "quality_contract"}
    assert all(len(item.sha256) == 64 for item in artifacts)


async def test_state_machine_missing_param(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_profiles: dict[str, uuid.UUID],
) -> None:
    """缺目标 Profile → WAITING_INPUT 并挂起澄清问题（AC-03 前置）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json={"requirement": "只给需求，不给画像"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "WAITING_INPUT"
    assert body["pending_question"]

    run = (
        await db_session.execute(select(AgentRun).where(AgentRun.id == uuid.UUID(body["id"])))
    ).scalar_one()
    assert run.status == "WAITING_INPUT"
    # 业务库只存静态问题文案，不存用户需求正文（FR-67）
    assert run.pending_question == body["pending_question"]
    assert "只给需求" not in (run.pending_question or "")


async def test_state_machine_invalid_profile_404(
    app: FastAPI, seeded: SeededWorkspace, seeded_profiles: dict[str, uuid.UUID]
) -> None:
    """引用不存在 / 跨项目的 Profile → 404 BIZ_404_PROFILE_NOT_FOUND（FR-66）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json=run_payload(seeded_profiles["source"], uuid.uuid4()),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_PROFILE_NOT_FOUND"
