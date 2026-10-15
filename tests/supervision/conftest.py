"""S8 监督 / 诊断 / 回滚用例共享夹具：最小可运行事实链。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 execution_runs /
runtime_supervision_snapshots / quality_reports
覆盖 FR：FR-45（监督）、FR-47（诊断）、FR-48（回滚）
对应 AC：AC-08（监督 / 诊断 / 回滚链路可驱动）

`seeded_execution`（tests/conftest.py）提供「已 Commit 的完整事实链」；
本文件补一个更轻的 `make_run`（只要 准备单 + 运行），供监督 / 诊断用例按需构造运行与指标。
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
)
from tests.helpers import SeededWorkspace

RunFactory = Callable[..., Awaitable[ExecutionRun]]


@pytest.fixture
async def make_run(
    db_session: AsyncSession, seeded: SeededWorkspace, frozen_version: tuple[uuid.UUID, str]
) -> RunFactory:
    """按需创建 (COMMITTED 准备单, ExecutionRun) 的工厂。

    与 `tests/execution/conftest.py::make_run` 同构（同准备单只能绑一条运行：
    SPEC 5.2 `harness_preparation_id` UK，故每次新建准备单）。
    """
    version_id, artifact_sha256 = frozen_version
    counter = {"n": 0}

    async def _make(status: str = "RUNNING", trigger: str = "MANUAL", **fields: object) -> ExecutionRun:
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
            idempotency_key=f"s8-{counter['n']}-{uuid.uuid4().hex[:6]}",
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
            **fields,  # type: ignore[arg-type]
        )
        db_session.add(run)
        await db_session.flush()
        return run

    return _make
