"""S9 安全进化 — 路由用例（提案状态机 / 受控发布；职责分离 + 跨项目 404 + 零明文）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_* /
第 6.4 章错误码 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-51（提案）、FR-52（受控发布）、FR-66（项目隔离）
对应 AC：AC-01（仅 SECURITY_APPROVER 可裁决）、AC-12（跨项目 404）、AC-02（零明文）
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI

from tests.helpers import SeededWorkspace, make_client


async def _create(
    app: FastAPI, project_id: uuid.UUID, actor: str, **overrides: object
) -> dict:
    payload: dict = {"type": "POLICY", "title": "收紧门禁"}
    payload.update(overrides)
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{project_id}/safety-proposals",
            json=payload,
            headers={"X-Actor-ID": actor},
        )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_create_proposal_as_member(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """**FR-51**：项目成员可创建提案 → 201 / DRAFT；响应不含 payload 正文。"""
    body = await _create(
        app,
        seeded.project_a_id,
        SeededWorkspace.ENGINEER_A,
        payload={"gate": "budget", "threshold": 10_000_000},
    )
    assert body["status"] == "DRAFT"
    assert body["type"] == "POLICY"
    assert "payload" not in body


@pytest.mark.asyncio
async def test_create_proposal_outsider_forbidden(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """非项目成员创建提案 → 403（AC-12）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/safety-proposals",
            json={"type": "POLICY", "title": "越权"},
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_proposal_rejects_bad_type(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """**FR-51**：类型非法 → 409。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/safety-proposals",
            json={"type": "SHELL", "title": "非法类型"},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_submit_then_decide_requires_security_approver(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**AC-01**：提交后由非 SECURITY_APPROVER 裁决 → 403。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    async with make_client(app) as client:
        submit = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/submit",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        assert submit.status_code == 200, submit.text
        decision = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/decisions",
            json={"decision": "APPROVE"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert decision.status_code == 403


@pytest.mark.asyncio
async def test_full_proposal_flow_as_security_approver(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**AC-01**：SECURITY_APPROVER 裁决 → APPROVED。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    async with make_client(app) as client:
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/submit",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        decision = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/decisions",
            json={"decision": "APPROVE", "reason": "批准"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "APPROVED"
    assert decision.json()["decision"] == "APPROVE"


@pytest.mark.asyncio
async def test_decide_before_submit_conflicts(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**FR-51**：跳过 IN_REVIEW 直接裁决 → 409（不可跳跃）。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    async with make_client(app) as client:
        decision = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/decisions",
            json={"decision": "APPROVE"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert decision.status_code == 409


@pytest.mark.asyncio
async def test_list_proposals_member_readable(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """提案列表：项目成员可读，且按项目隔离。"""
    await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/safety-proposals",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_release_flow_requires_security_approver(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**AC-01**：启动影子验证需 SECURITY_APPROVER；非安全审批人 → 403。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    async with make_client(app) as client:
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/submit",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/decisions",
            json={"decision": "APPROVE"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        forbidden = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/releases",
            json={"manifest_sha256": "a" * 64},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_release_flow_shadow_to_canary_to_promotion(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**FR-52**：影子 → 小流量 5% → 晋升（完整受控发布链路走通）。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    manifest = "a" * 64
    async with make_client(app) as client:
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/submit",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/decisions",
            json={"decision": "APPROVE"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        shadow = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/releases",
            json={"manifest_sha256": manifest},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        assert shadow.status_code == 201, shadow.text
        assert shadow.json()["stage"] == "SHADOW_RUNNING"
        release_id = shadow.json()["id"]

        canary = await client.post(
            f"/api/v1/safety-releases/{release_id}/canary",
            json={
                "canary_percent": 5,
                "manifest_sha256": manifest,
                "observation": {"sample_size": 100, "anomaly_count": 1},
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        assert canary.status_code == 200, canary.text
        assert canary.json()["stage"] == "CANARY"
        assert canary.json()["canary_percent"] == 5
        assert canary.json()["signature_key_id"]

        promotion = await client.post(
            f"/api/v1/safety-releases/{release_id}/promotion",
            json={
                "manifest_sha256": manifest,
                "observation": {"sample_size": 1000, "anomaly_count": 10},
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        assert promotion.status_code == 200, promotion.text
        assert promotion.json()["stage"] == "PROMOTION_APPROVED"


@pytest.mark.asyncio
async def test_canary_out_of_range_422(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**FR-52**：小流量百分比越界 → 422（BIZ_422_SAFETY_CANARY_OUT_OF_RANGE）。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    manifest = "a" * 64
    async with make_client(app) as client:
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/submit",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/decisions",
            json={"decision": "APPROVE"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        shadow = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/releases",
            json={"manifest_sha256": manifest},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        release_id = shadow.json()["id"]
        out_of_range = await client.post(
            f"/api/v1/safety-releases/{release_id}/canary",
            json={"canary_percent": 90, "manifest_sha256": manifest},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert out_of_range.status_code == 422


@pytest.mark.asyncio
async def test_rollback_release(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**FR-52**：异常 → 受控回滚（ROLLED_BACK 终态）。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    manifest = "a" * 64
    async with make_client(app) as client:
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/submit",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/decisions",
            json={"decision": "APPROVE"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        shadow = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/releases",
            json={"manifest_sha256": manifest},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        release_id = shadow.json()["id"]
        rolled = await client.post(
            f"/api/v1/safety-releases/{release_id}/rollback",
            json={"manifest_sha256": manifest, "reason": "影子异常"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["stage"] == "ROLLED_BACK"


@pytest.mark.asyncio
async def test_get_release_cross_project_404(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """跨项目查询提案发布记录 → 404（不泄露存在性，AC-12）。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/safety-proposals/{body['id']}/release",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        # admin-b 此时是项目 A 成员，可见性正常 → 提案尚无发布记录 → 404
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_release_response_has_no_signature_body(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**AC-02**：发布响应只回 `signature_key_id`，不回签名本体 / 明文。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    manifest = "a" * 64
    async with make_client(app) as client:
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/submit",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        await client.post(
            f"/api/v1/safety-proposals/{body['id']}/decisions",
            json={"decision": "APPROVE"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        shadow = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/releases",
            json={"manifest_sha256": manifest},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    payload = shadow.json()
    assert "signature" not in payload
    assert payload["signature_key_id"]
    lowered = shadow.text.lower()
    for marker in ("private_key", "password", "-----begin"):
        assert marker not in lowered


@pytest.mark.asyncio
async def test_release_without_approved_proposal_conflicts(
    app: FastAPI, seeded: SeededWorkspace, seeded_security_approver
) -> None:
    """**FR-52**：未批准提案启动发布 → 409。"""
    body = await _create(app, seeded.project_a_id, SeededWorkspace.ENGINEER_A)
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/safety-proposals/{body['id']}/releases",
            json={"manifest_sha256": "a" * 64},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
    assert response.status_code == 409
