"""S7 执行域共享夹具：可复用的运行工厂与 Harness 职责分配（API-11 需要 OPERATOR）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 execution_runs / 第 6.1 章 API-11/12/14
覆盖 FR：FR-38/39（触发与查询）、FR-66（项目隔离）
对应 AC：AC-06（执行链路可驱动）、AC-12（跨项目 403）

`seeded_execution`（tests/conftest.py）提供「已 Commit 的完整事实链」；本文件只补
「同一版本可建多条运行」的工厂与职责分配，避免每个用例重复拼装治理事实。
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import (
    ExecutionPreparation,
    ExecutionRun,
    HarnessPolicyDecision,
    ProjectMembership,
    ProjectRoleGrant,
)
from tests.helpers import SeededWorkspace

RunFactory = Callable[..., Awaitable[ExecutionRun]]


@pytest.fixture
async def make_run(
    db_session: AsyncSession, seeded: SeededWorkspace, frozen_version: tuple[uuid.UUID, str]
) -> RunFactory:
    """按需创建 (准备单, 运行) 的工厂（状态机分支用例需要多条独立运行）。

    同一准备单只能绑定一条运行（SPEC 5.2 `harness_preparation_id` UK），
    故每次调用都新建一张 COMMITTED 准备单。
    """
    version_id, artifact_sha256 = frozen_version
    counter = {"n": 0}

    async def _make(status: str = "QUEUED", trigger: str = "MANUAL") -> ExecutionRun:
        counter["n"] += 1
        decision = HarnessPolicyDecision(
            project_id=seeded.project_a_id,
            outcome="ALLOW",
            risk_level="P3",
            rule_evidence=[],
            policy_package_id="test-package",
            policy_package_sha256="0" * 64,
        )
        db_session.add(decision)
        await db_session.flush()
        preparation = ExecutionPreparation(
            project_id=seeded.project_a_id,
            policy_decision_id=decision.id,
            pipeline_version_id=version_id,
            status="COMMITTED",
            risk_level="P3",
            environment="dev",
            tool_name="pipeline.execute",
            tool_version="1.0.0",
            artifact_sha256=artifact_sha256,
            input_fingerprint="a" * 64,
            resource_fingerprint="b" * 64,
            impact_report={"requested_input": {}},
            rollback_plan={},
            supervision_contract={},
            required_roles=[],
            required_approvals=0,
            idempotency_key=f"exec-{counter['n']}-{uuid.uuid4().hex[:6]}",
            requested_by=seeded.engineer_a_user_id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db_session.add(preparation)
        await db_session.flush()
        run = ExecutionRun(
            pipeline_version_id=version_id,
            harness_preparation_id=preparation.id,
            status=status,
            trigger=trigger,
        )
        db_session.add(run)
        await db_session.flush()
        return run

    return _make


@pytest.fixture(autouse=True)
async def execution_roles(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> dict[str, uuid.UUID]:
    """执行域职责分配（autouse：API-11 需要 OPERATOR，缺职责表现为 403 而非断言失败）。

    - admin-a：项目 A 成员（查询 API-12/14 的合法主体）
    - admin-b：项目 A 成员 + OPERATOR（API-11 触发者）
    - outsider：不属项目 A（跨项目 403 用例）
    """
    db_session.add(
        ProjectMembership(
            project_id=seeded.project_a_id, user_id=seeded.admin_b_user_id, role="VIEWER"
        )
    )
    db_session.add(
        ProjectRoleGrant(
            project_id=seeded.project_a_id,
            user_id=seeded.admin_b_user_id,
            role="OPERATOR",
            granted_by=seeded.admin_a_user_id,
        )
    )
    await db_session.flush()
    return {
        "operator": seeded.admin_b_user_id,
        "viewer": seeded.admin_a_user_id,
        "outsider": seeded.outsider_user_id,
    }
