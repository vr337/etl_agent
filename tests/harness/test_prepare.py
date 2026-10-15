"""Prepare 准备单测试（T-12.5；S6 提示词第十二节 tests/harness）。

覆盖（FR-31）：指纹稳定、幂等键、PDP DENY 403 且无准备单落库、审批槽位按风险分配。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.errors import PolicyDeny
from etl_agent.infrastructure.models import ExecutionPreparation, HarnessPolicyDecision
from tests.helpers import SeededWorkspace, make_client

IDEMPOTENCY_KEY = "idem-prepare-001"


def _package() -> object:
    """仓库交付的验签策略包（与 lifespan 装配同源）。"""
    from etl_agent.config import settings
    from etl_agent.harness.policy.package import load_package

    return load_package(
        settings.policy_package_path,
        settings.policy_package_signature_path,
        settings.harness_public_key_list,
    )


def _body(version_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "pipeline_version_id": str(version_id),
        "idempotency_key": IDEMPOTENCY_KEY,
        "environment": "dev",
        "tool_name": "pipeline.execute",
        "tool_version": "1.0.0",
    }
    payload.update(overrides)
    return payload


async def _create(
    client: object, project_id: uuid.UUID, version_id: uuid.UUID, actor: str, **overrides: object
) -> object:
    response = await client.post(  # type: ignore[attr-defined]
        f"/api/v1/projects/{project_id}/preparations",
        json=_body(version_id, **overrides),
        headers={"X-Actor-ID": actor},
    )
    return response


async def test_prepare_fingerprint_stable(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """同输入两次创建（不同幂等键）→ 指纹一致（FR-31 指纹确定性）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        first = await _create(
            client, seeded.project_a_id, version_id, SeededWorkspace.ENGINEER_A,
            idempotency_key="idem-a",
        )
        second = await _create(
            client, seeded.project_a_id, version_id, SeededWorkspace.ENGINEER_A,
            idempotency_key="idem-b",
        )
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["input_fingerprint"] == second.json()["input_fingerprint"]
    assert first.json()["resource_fingerprint"] == second.json()["resource_fingerprint"]
    assert len(first.json()["input_fingerprint"]) == 64


async def test_prepare_idempotent(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """同幂等键同载荷 → 返回同一准备单（FR-31 幂等键去重）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        first = await _create(
            client, seeded.project_a_id, version_id, SeededWorkspace.ENGINEER_A
        )
        second = await _create(
            client, seeded.project_a_id, version_id, SeededWorkspace.ENGINEER_A
        )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    rows = (await db_session.execute(select(ExecutionPreparation))).scalars().all()
    assert len(rows) == 1


async def test_prepare_idempotency_conflict(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """同幂等键不同载荷 → 409 BIZ_409_IDEMPOTENCY_CONFLICT（FR-31）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        await _create(client, seeded.project_a_id, version_id, SeededWorkspace.ENGINEER_A)
        response = await _create(
            client,
            seeded.project_a_id,
            version_id,
            SeededWorkspace.ENGINEER_A,
            environment="prod",
        )
    assert response.status_code == 409
    assert response.json()["code"] == "BIZ_409_IDEMPOTENCY_CONFLICT"


async def test_prepare_deny_returns_403(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """PDP DENY（跨项目工具意图）→ 403 且**无准备单落库**（FR-29 / 禁止事项 19）。

    API-08 的 `actor_project_id` 由路径 project_id 提供（不可由请求体伪造），
    认证层已先拦非成员；本用例**在服务层直接注入跨项目 actor**，
    验证「无放行通道」：决策留证，但没有任何准备单被创建。
    """
    from etl_agent.harness.pdp.engine import PdpEngine
    from etl_agent.harness.prepare.service import PrepareInput, PrepareService

    version_id, _digest = frozen_version
    service = PrepareService(PdpEngine(_package()))
    with pytest.raises(PolicyDeny) as excinfo:
        await service.create_preparation(
            db_session,
            project_id=seeded.project_a_id,
            payload=PrepareInput(
                pipeline_version_id=version_id,
                idempotency_key=f"deny-{uuid.uuid4().hex[:8]}",
                environment="dev",
                tool_name="pipeline.execute",
                tool_version="1.0.0",
                # 跨项目工具意图 → HARNESS-P0-CROSS-PROJECT → P0 / DENY
                actor_project_id=seeded.project_b_id,
            ),
            requested_by=seeded.engineer_a_user_id,
        )
    assert excinfo.value.code == "HARNESS_403_POLICY_DENY"

    # 决策已留证（FR-29），但**没有任何准备单**（无放行通道，禁止事项 19）
    decisions = (
        await db_session.execute(select(HarnessPolicyDecision))
    ).scalars().all()
    preparations = (
        await db_session.execute(select(ExecutionPreparation))
    ).scalars().all()
    assert len(decisions) == 1 and decisions[0].outcome == "DENY"
    assert preparations == []
    # 证据只含规则 ID 与目标 / 来源项目（不含凭据与 Prompt，AC-02）
    assert "HARNESS-P0-CROSS-PROJECT" in str(decisions[0].rule_evidence)


async def test_prepare_requires_frozen_version(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline_version: uuid.UUID
) -> None:
    """未冻结版本 → 409（Prepare 前置，FR-31）。"""
    async with make_client(app) as client:
        response = await _create(
            client,
            seeded.project_a_id,
            seeded_pipeline_version,
            SeededWorkspace.ENGINEER_A,
            idempotency_key="idem-unfrozen",
        )
    assert response.status_code == 409
    assert response.json()["code"] == "BIZ_409_STATE_CONFLICT"


async def test_prepare_risk_slots(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """常规操作 → P3/ALLOW（无审批槽）；破坏性副作用 → P0 双槽（AC-01）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        routine = await _create(
            client,
            seeded.project_a_id,
            version_id,
            SeededWorkspace.ENGINEER_A,
            idempotency_key="idem-routine",
        )
        destructive = await _create(
            client,
            seeded.project_a_id,
            version_id,
            SeededWorkspace.ENGINEER_A,
            idempotency_key="idem-destructive",
            side_effect_type="CANCEL_EXECUTION",
        )
    assert routine.status_code == 201
    assert routine.json()["risk_level"] == "P3"
    assert routine.json()["required_approvals"] == 0
    assert routine.json()["status"] == "READY"

    assert destructive.status_code == 201
    assert destructive.json()["risk_level"] == "P0"
    assert destructive.json()["required_approvals"] == 2
    assert set(destructive.json()["required_roles"]) == {"DATA_APPROVER", "SECURITY_APPROVER"}
    assert destructive.json()["status"] == "WAITING_APPROVAL"


async def test_prepare_environments_validated(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """environment 不在 dev/staging/prod 内 → 422（Schema 层拒绝）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        response = await _create(
            client,
            seeded.project_a_id,
            version_id,
            SeededWorkspace.ENGINEER_A,
            environment="production",
        )
    assert response.status_code == 422
