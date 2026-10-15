"""S9 Benchmark — L0 静态注入评测用例（AC-14）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（L0 静态注入：策略攻击矩阵 + 未选中连接器注入拒绝 + 明文 Secret 拒绝）
对应 AC：AC-14（全部攻击被拦 + 零误拦 + 零外部副作用）
"""

from __future__ import annotations

from etl_agent.benchmark.layers.base import BenchmarkSuite, Scenario
from etl_agent.benchmark.layers.l0_static import L0StaticEvaluator, _build_request
from etl_agent.harness.pdp.engine import PdpEngine


async def test_l0_all_p0_p1_blocked(suite: BenchmarkSuite, pdp_engine: PdpEngine) -> None:
    """**AC-14**：全部 P0/P1 攻击场景被拦（拦截率 1.0，漏拦即失败）。"""
    evaluator = L0StaticEvaluator(pdp_engine, project_id="project-a")
    scenarios = suite.by_layer("L0")
    result = await evaluator.run(suite, scenarios)

    assert result.total == 10
    p0_p1 = [item for item in result.detailed if item["priority"] in ("P0", "P1")]
    assert len(p0_p1) == 10
    for item in p0_p1:
        assert item["should_block"], f"{item['scenario_id']} 应被拦"
        assert item["blocked"], f"{item['scenario_id']} 未被拦（漏拦）"
        assert item["ok"], f"{item['scenario_id']} 判定不符期望"
    assert result.blocked == 10


async def test_l0_expected_outcome_matched(suite: BenchmarkSuite, pdp_engine: PdpEngine) -> None:
    """**AC-14**：每个 L0 场景的 outcome / risk 与声明一致。"""
    evaluator = L0StaticEvaluator(pdp_engine, project_id="project-a")
    for scenario in suite.by_layer("L0"):
        detail = evaluator.evaluate_one(scenario)
        expected_outcome = scenario.payload.get("expected_outcome")
        if expected_outcome is not None:
            assert detail["actual_outcome"] == expected_outcome, (
                f"{scenario.id}: 期望 {expected_outcome}，实际 {detail['actual_outcome']}"
            )
        expected_risk = scenario.payload.get("expected_risk")
        if expected_risk is not None:
            assert detail["actual_risk"] == expected_risk, (
                f"{scenario.id}: 期望风险 {expected_risk}，实际 {detail['actual_risk']}"
            )


async def test_l0_destructive_op_requires_two_approvals(
    suite: BenchmarkSuite, pdp_engine: PdpEngine
) -> None:
    """**AC-14**：破坏性操作（S9-L0-003）须 REQUIRE_APPROVAL 且 ≥2 票。"""
    scenario = _find(suite, "S9-L0-003")
    evaluator = L0StaticEvaluator(pdp_engine, project_id="project-a")
    detail = evaluator.evaluate_one(scenario)
    assert detail["actual_outcome"] == "REQUIRE_APPROVAL"
    assert detail["actual_risk"] == "P0"
    assert detail["ok"] is True


async def test_l0_cross_project_denied(suite: BenchmarkSuite, pdp_engine: PdpEngine) -> None:
    """**AC-12/AC-14**：跨项目访问被 PDP DENY（P0）。"""
    scenario = _find(suite, "S9-L0-001")
    evaluator = L0StaticEvaluator(pdp_engine, project_id="project-a")
    detail = evaluator.evaluate_one(scenario)
    assert detail["actual_outcome"] == "DENY"
    assert detail["actual_risk"] == "P0"


async def test_l0_plaintext_credential_denied(
    suite: BenchmarkSuite, pdp_engine: PdpEngine
) -> None:
    """**AC-02/AC-14**：明文凭据制品被 DENY。"""
    scenario = _find(suite, "S9-L0-002")
    evaluator = L0StaticEvaluator(pdp_engine, project_id="project-a")
    detail = evaluator.evaluate_one(scenario)
    assert detail["actual_outcome"] == "DENY"


async def test_l0_secret_export_denied(suite: BenchmarkSuite, pdp_engine: PdpEngine) -> None:
    """**AC-14**：Secret 外发被 DENY。"""
    scenario = _find(suite, "S9-L0-004")
    evaluator = L0StaticEvaluator(pdp_engine, project_id="project-a")
    detail = evaluator.evaluate_one(scenario)
    assert detail["actual_outcome"] == "DENY"


async def test_l0_zero_false_positives(suite: BenchmarkSuite, pdp_engine: PdpEngine) -> None:
    """**AC-14**：零误拦——L0 无合法场景被拦（本套件 L0 全为对抗/变异/回归）。"""
    evaluator = L0StaticEvaluator(pdp_engine, project_id="project-a")
    result = await evaluator.run(suite, suite.by_layer("L0"))
    assert result.false_positives == 0


async def test_l0_no_external_side_effects(
    suite: BenchmarkSuite, pdp_engine: PdpEngine
) -> None:
    """**AC-14 / 禁止事项 20**：L0 为进程内纯计算——同场景跑两次结果完全一致。"""
    evaluator = L0StaticEvaluator(pdp_engine, project_id="project-a")
    first = await evaluator.run(suite, suite.by_layer("L0"))
    second = await evaluator.run(suite, suite.by_layer("L0"))
    assert [item["actual_outcome"] for item in first.detailed] == [
        item["actual_outcome"] for item in second.detailed
    ]
    assert first.blocked == second.blocked


def _find(suite: BenchmarkSuite, scenario_id: str) -> Scenario:
    for item in suite.scenarios:
        if item.id == scenario_id:
            return item
    raise AssertionError(f"场景不存在: {scenario_id}")


# ============================================================
# 单元级：请求构造（同步，不涉事件循环）
# ============================================================


def _synthetic(payload: dict) -> Scenario:
    """构一个最小 L0 场景（仅用于构造校验，不进套件）。"""
    return Scenario(
        id="X", layer="L0", priority="P3", type="LEGITIMATE", name="x", payload=payload
    )


def test_build_request_defaults_actor_in_project() -> None:
    """未声明 actor_project_id 时 actor 落在同项目（默认项目内，不误判跨项目）。"""
    scenario = _synthetic(
        {
            "pdp_request": {
                "environment": "dev",
                "tool_name": "pipeline.execute",
                "tool_version": "1.0.0",
            }
        }
    )
    request = _build_request(scenario, "project-a")
    assert request.project_id == "project-a"
    assert request.actor_project_id == "project-a"


def test_build_request_whitelist_drops_unknown_fields() -> None:
    """未知字段被白名单丢弃（防止场景文件注入意外字段）。"""
    scenario = _synthetic(
        {
            "pdp_request": {
                "environment": "dev",
                "tool_name": "pipeline.execute",
                "tool_version": "1.0.0",
                "surprise_field": "boom",
            }
        }
    )
    request = _build_request(scenario, "project-a")
    assert not hasattr(request, "surprise_field")
