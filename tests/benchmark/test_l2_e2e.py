"""S9 Benchmark — L2 真实链路垂直切片用例（AC-16，mock 引擎）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（L2 真实链路：连接 → Harness 协议 → 影子表 → 分流 → Swap SQL 生成）
对应 AC：AC-16（端到端垂直切片走通；S9 用 mock 引擎，真实引擎留 S10 前）
"""

from __future__ import annotations

from etl_agent.benchmark.layers.base import BenchmarkSuite, Scenario
from etl_agent.benchmark.layers.l2_e2e import (
    DEFAULT_TARGET_TABLE,
    L2E2EEvaluator,
    MockEngineRun,
)
from etl_agent.execution.atomic_swap import build_swap_sql
from etl_agent.execution.shadow import create_shadow_table_sql, shadow_table_name


async def test_l2_vertical_slice_passes(suite: BenchmarkSuite) -> None:
    """**AC-16**：端到端垂直切片（影子表 → 分流 → Swap SQL）走通。"""
    evaluator = L2E2EEvaluator()
    result = await evaluator.run(suite, suite.by_layer("L2"))
    assert result.total == 1
    detail = result.detailed[0]
    assert detail["scenario_id"] == "S9-L2-001"
    assert detail["ok"] is True
    assert result.passed == 1


async def test_l2_produces_shadow_table_and_swap_sql(suite: BenchmarkSuite) -> None:
    """**AC-16**：切片产出影子表 DDL 与 Swap SQL（真实模板，非伪造）。"""
    scenario = _find(suite, "S9-L2-001")
    evaluator = L2E2EEvaluator()
    detail = await evaluator.evaluate_one(scenario)

    target = DEFAULT_TARGET_TABLE
    shadow = shadow_table_name(target)
    assert detail["target_table"] == target
    assert detail["shadow_table"] == shadow
    assert detail["shadow_ddl"] == create_shadow_table_sql(target)
    assert detail["swap_sql"] == build_swap_sql(target, shadow)


async def test_l2_routing_valid_and_error_sinks_distinct(suite: BenchmarkSuite) -> None:
    """**AC-16**：分流路由正确——valid 指向影子表、error 指向独立错误表。"""
    scenario = _find(suite, "S9-L2-001")
    evaluator = L2E2EEvaluator()
    detail = await evaluator.evaluate_one(scenario)
    assert detail["valid_sink"] != detail["error_sink"]
    assert detail["valid_sink"] == shadow_table_name(DEFAULT_TARGET_TABLE)
    assert detail["error_sink"].endswith("_error")


async def test_l2_mock_engine_records_submission(suite: BenchmarkSuite) -> None:
    """**AC-16 / 禁止事项 20**：引擎用替身——提交被记录但无真实网络调用。"""
    scenario = _find(suite, "S9-L2-001")
    evaluator = L2E2EEvaluator()
    await evaluator.evaluate_one(scenario)
    assert len(evaluator.engine.submitted) == 1
    assert evaluator.engine.stopped == []


async def test_l2_deterministic_output(suite: BenchmarkSuite) -> None:
    """**AC-16**：同场景重复执行产出完全一致（可复现）。"""
    scenario = _find(suite, "S9-L2-001")
    first = await L2E2EEvaluator().evaluate_one(scenario)
    second = await L2E2EEvaluator().evaluate_one(scenario)
    assert first["shadow_ddl"] == second["shadow_ddl"]
    assert first["swap_sql"] == second["swap_sql"]
    assert first["job_id"] == second["job_id"]


async def test_mock_engine_submit_and_stop() -> None:
    """替身引擎：submit 返回可辨识 job_id、stop 被记录。"""
    engine = MockEngineRun()
    job_id = await engine.submit_job("hocon-body", job_name="j1")
    assert job_id == "mock-job-1"
    await engine.stop_job(job_id)
    assert engine.stopped == ["mock-job-1"]


def _find(suite: BenchmarkSuite, scenario_id: str) -> Scenario:
    for item in suite.scenarios:
        if item.id == scenario_id:
            return item
    raise AssertionError(f"场景不存在: {scenario_id}")
