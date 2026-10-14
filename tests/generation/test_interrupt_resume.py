"""AC-03 核心：LangGraph 中断与同 thread_id 恢复（T-09；S5 提示词第十二节）。

覆盖：参数缺失 → WAITING_INPUT → 回答 → 同 thread_id 恢复 → 继续生成；
非中断态收到回答 → 422 BIZ_422_INTERRUPT_NOT_PENDING。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import AgentRun
from tests.helpers import SeededWorkspace, make_client


async def _create(client: object, project_id: uuid.UUID, body: dict[str, object]) -> dict:
    response = await client.post(  # type: ignore[attr-defined]
        f"/api/v1/projects/{project_id}/agent-runs",
        json=body,
        headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _answer(client: object, run_id: str, answer: str) -> tuple[int, dict]:
    response = await client.post(  # type: ignore[attr-defined]
        f"/api/v1/agent-runs/{run_id}/messages",
        json={"answer": answer},
        headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
    )
    return response.status_code, response.json()


async def test_interrupt_resume(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_profiles: dict[str, uuid.UUID],
) -> None:
    """中断 → 两次回答 → 同 thread_id 恢复并完成生成（AC-03）。"""
    async with make_client(app) as client:
        created = await _create(
            client, seeded.project_a_id, {"requirement": "把 orders 汇总成 orders_wide"}
        )
        # 1) 缺来源 / 目标画像 → 挂起澄清
        assert created["status"] == "WAITING_INPUT"
        thread_id = created["thread_id"]
        first_question = created["pending_question"]
        assert first_question

        # 2) 回答来源画像 → 仍缺目标画像 → 再次挂起（新中断）
        status_code, body = await _answer(client, created["id"], str(seeded_profiles["source"]))
        assert status_code == 200
        assert body["status"] == "WAITING_INPUT"
        assert body["pending_question"] != first_question

        # 3) 回答目标画像 → 参数齐备 → 生成 → 门禁通过 → 等审批
        status_code, body = await _answer(client, created["id"], str(seeded_profiles["sink"]))
        assert status_code == 200
        assert body["status"] == "WAITING_APPROVAL"
        # 同一 thread_id 贯穿三次调用（跨请求恢复，FR-17）
        assert body["thread_id"] == thread_id

    run = (
        await db_session.execute(select(AgentRun).where(AgentRun.id == uuid.UUID(created["id"])))
    ).scalar_one()
    assert run.status == "WAITING_APPROVAL"
    assert run.pending_question is None
    assert run.generation_candidate_count == 1


async def test_interrupt_not_pending(
    app: FastAPI, seeded: SeededWorkspace, seeded_profiles: dict[str, uuid.UUID]
) -> None:
    """非中断态收到澄清回答 → 422 BIZ_422_INTERRUPT_NOT_PENDING（FR-17）。"""
    async with make_client(app) as client:
        created = await _create(
            client,
            seeded.project_a_id,
            {
                "requirement": "参数齐备，直接生成",
                "source_profile_ids": [str(seeded_profiles["source"])],
                "sink_profile_ids": [str(seeded_profiles["sink"])],
            },
        )
        assert created["status"] == "WAITING_APPROVAL"
        status_code, body = await _answer(client, created["id"], "多余的澄清回答")
    assert status_code == 422
    assert body["code"] == "BIZ_422_INTERRUPT_NOT_PENDING"


async def test_resume_unknown_run_404(app: FastAPI, seeded: SeededWorkspace) -> None:
    """未知运行 → 404 BIZ_404_AGENT_RUN_NOT_FOUND（不泄露存在性）。"""
    async with make_client(app) as client:
        status_code, body = await _answer(client, str(uuid.uuid4()), "任意回答")
    assert status_code == 404
    assert body["code"] == "BIZ_404_AGENT_RUN_NOT_FOUND"


async def test_resume_cross_project_403(
    app: FastAPI, seeded: SeededWorkspace, seeded_profiles: dict[str, uuid.UUID]
) -> None:
    """局外人对他人运行澄清 → 403 AUTH_403_NOT_PROJECT_MEMBER（AC-12）。"""
    async with make_client(app) as client:
        created = await _create(
            client, seeded.project_a_id, {"requirement": "缺画像，先挂起"}
        )
        response = await client.post(
            f"/api/v1/agent-runs/{created['id']}/messages",
            json={"answer": "试图越权回答"},
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"
