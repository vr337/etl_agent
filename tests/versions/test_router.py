"""版本路由测试（T-11.7 API-07；S6 提示词第十二节 tests/versions）。

覆盖（FR-26/27/28）：API-07 冻结返回值、跨项目 403、未冻结 409、派生、审批决策、Diff。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import PipelineVersion
from etl_agent.versions.state import STATUS_VALIDATED
from tests.conftest import S6_TEST_HOCON, S6_TEST_PLAN
from tests.helpers import SeededWorkspace, make_client


async def test_router_freeze(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_pipeline_version: uuid.UUID,
) -> None:
    """API-07：DRAFT 版本无正文可冻结 → 409；用 Checkpoint 取回正文后冻结成功（FR-26）。

    S5 生成的版本在门禁通过时已自动冻结，故此处用「显式提供正文」验证写入路径。
    """
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/pipeline-versions/{seeded_pipeline_version}/freeze",
            json={"plan": S6_TEST_PLAN, "hocon": S6_TEST_HOCON},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 409  # 未 GENERATED，未产出候选
    assert response.json()["code"] == "BIZ_409_STATE_CONFLICT"

    # 先产出候选（GENERATED）再冻结 → 成功返回 SHA256
    from etl_agent.versions.service import load_version, mark_generated

    version, _ = await load_version(db_session, seeded_pipeline_version)
    await mark_generated(db_session, version.id)
    await db_session.commit()

    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/pipeline-versions/{seeded_pipeline_version}/freeze",
            json={"plan": S6_TEST_PLAN, "hocon": S6_TEST_HOCON},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["artifact_sha256"]) == 64
    assert body["status"] == STATUS_VALIDATED
    assert {item["artifact_type"] for item in body["artifacts"]} == {
        "plan",
        "hocon",
        "quality_contract",
    }


async def test_router_freeze_not_frozen_409(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline_version: uuid.UUID
) -> None:
    """从未生成的版本冻结 → 409（无摘要可返回）。"""
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
    """未知版本 → 404 BIZ_404_NOT_FOUND。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/pipeline-versions/{uuid.uuid4()}/freeze",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_NOT_FOUND"


async def test_router_derive_and_approve(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    design_approvers: dict[str, uuid.UUID],
    frozen_version: tuple,
) -> None:
    """派生 → 审批：派生版本、PENDING 申请 APPROVE 后版本转终态（FR-26/28）。

    `design_approvers` 让 admin-b 加入 A 项目并持 SECURITY_APPROVER——
    设计审批必须「非申请人 + 持审批职责」（PRD 2.3-1）。
    """
    version_id, digest = frozen_version

    async with make_client(app) as client:
        derived = await client.post(
            f"/api/v1/pipeline-versions/{version_id}/derive",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        assert derived.status_code == 201
        derived_id = derived.json()["id"]
        assert derived.json()["parent_version_id"] == str(version_id)

        created = await client.post(
            f"/api/v1/pipeline-versions/{version_id}/approval-requests",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        assert created.status_code == 201
        request_id = created.json()["id"]
        assert created.json()["artifact_sha256"] == digest
        assert created.json()["status"] == "PENDING"

        # 维度审批需「非申请人且持审批职责」：让 engineer-a 再提交一份，
        # 由同项目的另一个 DATA_APPROVER（engineer-a 自己也是）决策会触发自批，
        # 因此这里用 ADMIN_B（SECURITY_APPROVER）决策 APPROVE。
        approved = await client.post(
            f"/api/v1/approval-requests/{request_id}/decisions",
            json={"decision": "APPROVE", "reason": "Schema 对齐"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "APPROVED"
        assert approved.json()["version_status"] == "APPROVED"

    version = await db_session.get(PipelineVersion, version_id)
    assert version is not None and version.status == "APPROVED"
    assert str(derived_id) != str(version_id)


async def test_router_approval_self_forbidden(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """设计审批申请人自批 → 409 HARNESS_409_SELF_APPROVAL（PRD 2.3-1）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/pipeline-versions/{version_id}/approval-requests",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        assert created.status_code == 201
        response = await client.post(
            f"/api/v1/approval-requests/{created.json()['id']}/decisions",
            json={"decision": "APPROVE", "reason": "自己批自己"},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "HARNESS_409_SELF_APPROVAL"


async def test_router_approval_role_required(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """无设计审批职责 → 403 AUTH_403_ROLE_REQUIRED（ADMIN 不隐式升级，FR-03）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/pipeline-versions/{version_id}/approval-requests",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert created.status_code == 201
        response = await client.post(
            f"/api/v1/approval-requests/{created.json()['id']}/decisions",
            json={"decision": "APPROVE", "reason": "越权审批"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_ROLE_REQUIRED"


async def test_router_diff_versions(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple,
) -> None:
    """版本 Diff：摘要级差异恒可用；内容不可得时降级（FR-27 / S5 吸收 2）。"""
    version_id, _ = frozen_version
    from etl_agent.versions.service import derive_version, load_version

    _, project_id = await load_version(db_session, version_id)
    derived = await derive_version(db_session, version_id, project_id=project_id)

    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/pipeline-versions/{version_id}/diff",
            params={"against_version_id": str(derived.id)},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["artifact_digests"]["plan"]["changed"] is True
    assert body["content_available"] is False  # 派生版本无生成运行 → 无正文可取
    assert body["schema_diff"] is None


async def test_router_version_cross_project_403(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """局外人访问版本端点 → 403（AC-12）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/pipeline-versions/{version_id}",
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403
