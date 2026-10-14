"""生成遥测测试（T-09；S5 提示词第十二节 test_telemetry_no_prompt）。

覆盖（FR-17 / FR-67）：`agent_runs` 无 prompt / candidate / hocon 字段；
遥测只含计数 / 耗时 / 非敏感诊断；诊断净化函数剔除正文键。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.generation.telemetry import FORBIDDEN_DIAGNOSIS_KEYS, sanitize_diagnosis
from etl_agent.infrastructure.models import AgentRun
from tests.helpers import SeededWorkspace, make_client

# 遥测诊断允许出现的键（只有计数 / 耗时 / 失败分类）
ALLOWED_DIAGNOSIS_KEYS = {"candidate_count", "repair_count", "duration_ms", "failure_category"}

# `agent_runs` 字段白名单（SPEC 5.2 + FR-17/67）：只有状态、挂起问题与 6 个非敏感遥测字段。
# 白名单式断言是「无 Prompt / 候选正文字段」的最强证明——任何承载正文的新列都会让它失败。
# 注意：`generation_candidate_count` 是 FR-17 明确要求的**计数**字段（含 "candidate" 字样但只存数字），
# 与「候选正文」是两回事。
ALLOWED_AGENT_RUN_COLUMNS = {
    "id",
    "created_at",
    "updated_at",
    "pipeline_version_id",
    "thread_id",
    "status",
    "pending_question",
    "source_profile_ids",
    "sink_profile_ids",
    "file_asset_ids",
    "generation_backend",
    "generation_model",
    "generation_candidate_count",
    "generation_repair_count",
    "generation_duration_ms",
    "generation_diagnosis",
}


async def test_telemetry_no_prompt(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_profiles: dict[str, uuid.UUID],
) -> None:
    """遥测只记计数 / 耗时 / 非敏感诊断，业务库无 Prompt 与候选正文（FR-17/67）。"""
    requirement_marker = f"REQ-MARKER-{uuid.uuid4().hex}"
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json={
                "requirement": requirement_marker,
                "source_profile_ids": [str(seeded_profiles["source"])],
                "sink_profile_ids": [str(seeded_profiles["sink"])],
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "WAITING_APPROVAL"
    # 响应也不含需求正文（AC-02 / FR-67）
    assert requirement_marker not in response.text

    columns = (
        (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'agent_runs'"
                )
            )
        )
        .scalars()
        .all()
    )
    # 字段白名单：多出任何一列（尤其是正文列）都会失败
    assert set(columns) == ALLOWED_AGENT_RUN_COLUMNS
    for name in columns:
        assert "prompt" not in name.lower()
        assert "hocon" not in name.lower()
        assert "requirement" not in name.lower()

    run = (
        await db_session.execute(select(AgentRun).where(AgentRun.id == uuid.UUID(body["id"])))
    ).scalar_one()
    assert run.generation_backend
    assert run.generation_model
    assert run.generation_candidate_count == 1
    assert run.generation_repair_count == 0
    assert run.generation_duration_ms is not None and run.generation_duration_ms >= 0
    assert set(run.generation_diagnosis or {}) <= ALLOWED_DIAGNOSIS_KEYS
    assert requirement_marker not in str(run.generation_diagnosis)


def test_sanitize_diagnosis_strips_forbidden_keys() -> None:
    """净化函数剔除正文键——即使上游误传也不会写进业务库（FR-67 最后一道闸）。"""
    assert FORBIDDEN_DIAGNOSIS_KEYS
    payload = {
        "requirement": "需求正文",
        "hocon": "sink { }",
        "etl_plan": {"a": 1},
        "candidate": "正文",
        "candidate_count": 2,
        "duration_ms": 12,
    }
    assert sanitize_diagnosis(payload) == {"candidate_count": 2, "duration_ms": 12}
    assert sanitize_diagnosis(None) == {}
