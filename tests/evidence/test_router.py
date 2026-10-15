"""S9 证据账本 — API 路由用例（查询证据流 / 事件列表 / 整流验证）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 8 章「安全 · 多租户」
覆盖 FR：FR-36（证据账本查询与验证）、FR-66（项目隔离）
对应 AC：AC-12（跨项目 404）、AC-02（响应零明文）
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.evidence import EvidenceEventInput, append_event
from tests.helpers import SeededWorkspace, make_client

pytestmark = pytest.mark.asyncio

ADMIN_HEADERS = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
OUTSIDER_HEADERS = {"X-Actor-ID": SeededWorkspace.OUTSIDER}
ADMIN_B_HEADERS = {"X-Actor-ID": SeededWorkspace.ADMIN_B}


async def _seed_events(session: AsyncSession, project_id, count: int = 3) -> None:
    for index in range(count):
        await append_event(
            session,
            project_id=project_id,
            event=EvidenceEventInput(event_type="TEST", payload={"i": index}),
        )


async def test_router_get_stream(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """查询证据流返回 head_hash（成员可读）。"""
    await _seed_events(db_session, seeded.project_a_id)
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/evidence-stream",
            headers=ADMIN_HEADERS,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(seeded.project_a_id)
    assert body["head_hash"] and len(body["head_hash"]) == 64


async def test_router_list_events(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """事件列表按序号增量拉取（after_sequence 游标）。"""
    await _seed_events(db_session, seeded.project_a_id, count=5)
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/evidence-stream/events"
            "?after_sequence=2&limit=10",
            headers=ADMIN_HEADERS,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["event_count"] == 3
    assert [item["sequence"] for item in body["events"]] == [3, 4, 5]


async def test_router_verify_valid(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """整流验证端点返回完整报告。"""
    await _seed_events(db_session, seeded.project_a_id)
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/evidence-stream/verify",
            headers=ADMIN_HEADERS,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"valid", "event_count", "head_hash", "errors"}
    assert body["valid"] is True
    assert body["event_count"] == 3


async def test_router_stream_not_found(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """项目无证据流 → 404（不自动建流，查询无副作用）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/evidence-stream",
            headers=ADMIN_HEADERS,
        )
    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_EVIDENCE_STREAM_NOT_FOUND"


async def test_router_cross_project_denied(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-12**：B 项目管理员访问 A 项目证据流 → 403/404（不泄露存在性）。"""
    await _seed_events(db_session, seeded.project_a_id)
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/evidence-stream",
            headers=ADMIN_B_HEADERS,
        )
    assert response.status_code in (403, 404)


async def test_router_non_member_denied(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """局外人访问 → 403（非项目成员）。"""
    await _seed_events(db_session, seeded.project_a_id)
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/evidence-stream",
            headers=OUTSIDER_HEADERS,
        )
    assert response.status_code == 403


async def test_router_zero_plaintext(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-02**：证据响应不含 payload 明文。"""
    marker = "router-plaintext-marker-xyz"
    await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={"v": marker}),
    )
    async with make_client(app) as client:
        events = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/evidence-stream/events",
            headers=ADMIN_HEADERS,
        )
        verify = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/evidence-stream/verify",
            headers=ADMIN_HEADERS,
        )
    assert marker not in events.text
    assert marker not in verify.text
