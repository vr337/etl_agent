"""门禁服务与有限修复预算测试（T-10；S5 提示词第十二节 tests/gate）。

覆盖：候选预算 ≤3（含初始）耗尽 → 确定性失败；第 2 份候选通过 → 成功；
通过门禁后冻结 SHA256；门禁失败反馈只含规则 ID + 脱敏摘要。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.gate.engine_check import EngineCheckResult
from etl_agent.gate.service import HARD_CAP_CANDIDATES, resolve_budget, run_gate
from etl_agent.infrastructure.models import AgentRun, PipelineVersion
from etl_agent.llm.mock_provider import compliant_hocon, compliant_plan
from etl_agent.llm.provider import GeneratedCandidate
from tests.gate.test_rules import COMPLIANT_HOCON, base_context, base_plan
from tests.helpers import SeededWorkspace, make_client, make_settings


class ScriptedProvider:
    """按调用序号返回脚本化候选：`valid_from` 之前的候选故意不合规（GATE-002 拦截）。"""

    def __init__(self, *, valid_from: int) -> None:
        self.valid_from = valid_from
        self.calls = 0
        self.feedbacks: list[str | None] = []

    async def generate_etl_plan(
        self,
        requirement: str,
        source_profile: dict[str, Any],
        sink_profile: dict[str, Any],
        feedback: str | None = None,
    ) -> GeneratedCandidate:
        self.calls += 1
        self.feedbacks.append(feedback)
        plan = compliant_plan(requirement, source_profile, sink_profile)
        if self.calls < self.valid_from:
            plan["source"]["connector_type"] = "MONGODB"
        return GeneratedCandidate(
            etl_plan=plan,
            hocon=compliant_hocon(source_profile, sink_profile),
            backend="mock",
            model="scripted",
        )


def _payload(source_id: uuid.UUID, sink_id: uuid.UUID) -> dict[str, object]:
    return {
        "requirement": "把 orders 汇总成 orders_wide",
        "source_profile_ids": [str(source_id)],
        "sink_profile_ids": [str(sink_id)],
    }


def test_finite_repair_budget_3() -> None:
    """预算硬口径：含初始候选最多 3 份、最多 2 轮修复（FR-21 / 禁止事项 15）。"""
    budget = resolve_budget(make_settings(llm_max_candidates=3))
    assert HARD_CAP_CANDIDATES == 3
    assert budget.max_candidates == 3
    assert budget.allows_next(0) and budget.allows_next(2)
    assert not budget.allows_next(3)
    assert budget.exhausted(3)
    assert budget.repair_rounds() == 2
    # 配置越界也再夹一次硬上限（防御性收敛）
    assert resolve_budget(make_settings(llm_max_candidates=9)).max_candidates == 3


async def test_repair_budget_exhausted_after_3_candidates(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_profiles: dict[str, uuid.UUID],
) -> None:
    """3 份候选全部不合规 → 预算耗尽 → 422 + 运行置 FAILED（FR-21）。"""
    provider = ScriptedProvider(valid_from=99)
    app.state.llm_provider = provider
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json=_payload(seeded_profiles["source"], seeded_profiles["sink"]),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "BIZ_422_LLM_BUDGET_EXHAUSTED"
    # 候选数恰为上限 3（不多生成）
    assert provider.calls == 3

    runs = (await db_session.execute(select(AgentRun))).scalars().all()
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "FAILED"
    assert run.generation_candidate_count == 3
    assert run.generation_repair_count == 2
    assert (run.generation_diagnosis or {}).get("failure_category") == "LLM_BUDGET_EXHAUSTED"


async def test_finite_repair_success_on_2(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_profiles: dict[str, uuid.UUID],
) -> None:
    """第 1 份被门禁拦截 → 第 2 份通过 → 成功（AC-04）。"""
    provider = ScriptedProvider(valid_from=2)
    app.state.llm_provider = provider
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json=_payload(seeded_profiles["source"], seeded_profiles["sink"]),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "WAITING_APPROVAL"
    assert body["generation_candidate_count"] == 2
    assert body["generation_repair_count"] == 1

    # 第 1 次调用无反馈；第 2 次调用的反馈只含规则 ID + 脱敏摘要，不含 HOCON 正文（11.7）
    assert provider.feedbacks[0] is None
    feedback = provider.feedbacks[1] or ""
    assert "GATE-002" in feedback
    assert "password" not in feedback
    assert "sink {" not in feedback
    assert len(feedback) < 300


async def test_freeze_sha256_after_gate_pass(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_profiles: dict[str, uuid.UUID],
) -> None:
    """通过门禁 → 冻结 SHA256；API-07 幂等返回同一摘要（FR-26 / API-07）。"""
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json=_payload(seeded_profiles["source"], seeded_profiles["sink"]),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert created.status_code == 201
        run = (
            await db_session.execute(
                select(AgentRun).where(AgentRun.id == uuid.UUID(created.json()["id"]))
            )
        ).scalar_one()
        version = await db_session.get(PipelineVersion, run.pipeline_version_id)
        assert version is not None and version.artifact_sha256
        frozen = version.artifact_sha256

        first = await client.post(
            f"/api/v1/pipeline-versions/{version.id}/freeze",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        second = await client.post(
            f"/api/v1/pipeline-versions/{version.id}/freeze",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert first.status_code == 200
    assert first.json()["artifact_sha256"] == frozen
    assert first.json()["status"] == "VALIDATED"
    assert second.json()["artifact_sha256"] == frozen


async def test_run_gate_engine_failure_blocks() -> None:
    """引擎检查失败 → 门禁不通过（FR-20 前置：机器门禁通过才进人工审批）。"""

    class FailingChecker:
        name = "stub-failing"

        async def check(self, hocon: str) -> EngineCheckResult:
            return EngineCheckResult(ok=False, checker=self.name, summary="引擎结构检查失败")

    result = await run_gate(base_plan(), COMPLIANT_HOCON, base_context(), engine_checker=FailingChecker())
    assert not result.passed
    assert result.failures == []  # 14 项静态契约全过，是引擎检查拦下的
    assert result.engine is not None and not result.engine.ok
