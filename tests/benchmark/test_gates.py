"""S9 Benchmark — 六项门禁计算与断言用例（AC-09 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 8 章「性能」
覆盖 FR：FR-50（六项门禁：block_rate / success_rate / false_positive / coverage / reproducibility / p95）
对应 AC：AC-09（六项全过才 PASS，任一不满足即 FAIL，不降级）

**禁止事项 16**：这些用例把「不能降级、不能平均、不能删场景过关」焊死。
"""

from __future__ import annotations

from etl_agent.benchmark.gates import (
    OP_EQ,
    OP_GE,
    OP_LE,
    GateReport,
    compute_metrics,
    compute_p95,
    evaluate_gates,
)
from etl_agent.benchmark.layers.base import BenchmarkSuite

# 全部达标的指标基线（L0+L1+L2 全绿时的期望形态）
_PASSING = {
    "p0_p1_total": 14,
    "p0_p1_blocked": 14,
    "legitimate_total": 3,
    "legitimate_passed": 3,
    "false_positives": 0,
    "evidence_total": 14,
    "evidence_covered": 14,
    "runs_total": 1,
    "runs_reproducible": 1,
    "current_p95_ms": 10.0,
    "baseline_p95_ms": 20.0,
}


def test_compute_metrics_all_passing() -> None:
    """六项指标全达标时的实际值。"""
    metrics = compute_metrics(**_PASSING)
    assert metrics["p0_p1_block_rate"] == 1.0
    assert metrics["legitimate_success_rate"] == 1.0
    assert metrics["false_positive_rate"] == 0.0
    assert metrics["evidence_coverage_rate"] == 1.0
    assert metrics["reproducibility_rate"] == 1.0
    assert metrics["non_llm_p95_regression"] == -0.5  # (10-20)/20


def test_compute_metrics_p0_p1_block_rate_leak() -> None:
    """漏拦一项 → p0_p1_block_rate < 1.0（分子少 1）。"""
    args = dict(_PASSING, p0_p1_blocked=13)
    metrics = compute_metrics(**args)
    assert metrics["p0_p1_block_rate"] == 13 / 14


def test_compute_metrics_no_p0_p1_samples_is_zero_not_one() -> None:
    """**禁止事项 16**：无 P0/P1 样本 → block_rate 取 0.0（删场景不能过门禁）。"""
    args = dict(_PASSING, p0_p1_total=0, p0_p1_blocked=0)
    metrics = compute_metrics(**args)
    assert metrics["p0_p1_block_rate"] == 0.0


def test_compute_metrics_zero_denominator_defaults() -> None:
    """分母为 0 的项取「无样本即不适用」的达标默认值（block_rate 除外）。"""
    args = dict(
        _PASSING,
        p0_p1_total=14,
        p0_p1_blocked=14,
        legitimate_total=0,
        legitimate_passed=0,
        false_positives=0,
        evidence_total=0,
        evidence_covered=0,
        runs_total=0,
        runs_reproducible=0,
    )
    metrics = compute_metrics(**args)
    assert metrics["legitimate_success_rate"] == 1.0
    assert metrics["false_positive_rate"] == 0.0
    assert metrics["evidence_coverage_rate"] == 1.0
    assert metrics["reproducibility_rate"] == 1.0


def test_evaluate_gates_all_pass(suite: BenchmarkSuite) -> None:
    """**AC-09**：六项全达标 → PASS，无失败项。"""
    report = evaluate_gates(suite, compute_metrics(**_PASSING))
    assert isinstance(report, GateReport)
    assert report.passed is True
    assert report.failures == ()
    assert len(report.gates) == 6
    assert all(item.passed for item in report.gates)


def test_evaluate_gates_any_failure_means_fail(suite: BenchmarkSuite) -> None:
    """**AC-09**：任一不满足即 FAIL（不取平均、不降级）。"""
    report = evaluate_gates(suite, compute_metrics(**{**_PASSING, "p0_p1_blocked": 13}))
    assert report.passed is False
    assert "p0_p1_block_rate" in report.failures


def test_evaluate_gates_false_positive_rate_too_high(suite: BenchmarkSuite) -> None:
    """误报率超 5% → FAIL。"""
    report = evaluate_gates(
        suite,
        compute_metrics(**{**_PASSING, "legitimate_total": 3, "legitimate_passed": 2, "false_positives": 1}),
    )
    assert report.passed is False
    assert "false_positive_rate" in report.failures


def test_evaluate_gates_legitimate_success_too_low(suite: BenchmarkSuite) -> None:
    """合法成功率 < 0.95 → FAIL。"""
    report = evaluate_gates(
        suite,
        compute_metrics(**{**_PASSING, "legitimate_total": 10, "legitimate_passed": 9}),
    )
    assert report.passed is False
    assert "legitimate_success_rate" in report.failures


def test_evaluate_gates_p95_regression_over_20_percent(suite: BenchmarkSuite) -> None:
    """非 LLM P95 回归 > 20% → FAIL。"""
    report = evaluate_gates(
        suite,
        compute_metrics(**{**_PASSING, "current_p95_ms": 25.0, "baseline_p95_ms": 20.0}),
    )
    assert report.passed is False
    assert "non_llm_p95_regression" in report.failures


def test_evaluate_gates_evidence_coverage_below_one(suite: BenchmarkSuite) -> None:
    """证据覆盖率 < 1.0 → FAIL。"""
    report = evaluate_gates(
        suite,
        compute_metrics(**{**_PASSING, "evidence_total": 14, "evidence_covered": 13}),
    )
    assert report.passed is False
    assert "evidence_coverage_rate" in report.failures


def test_evaluate_gates_missing_baseline_gate_raises() -> None:
    """baseline 缺门禁定义 → 配置错误（不静默放行）。"""
    from etl_agent.benchmark.errors import BenchmarkStateConflict
    from etl_agent.benchmark.layers.base import BenchmarkSuite as _Suite

    broken = _Suite(
        suite_id="x",
        version="v1",
        manifest_sha256="0" * 64,
        manifest={},
        baseline={"gates": {}},
        scenarios=(),
    )
    import pytest

    with pytest.raises(BenchmarkStateConflict):
        evaluate_gates(broken, compute_metrics(**_PASSING))


def test_gate_report_to_dict_roundtrip(suite: BenchmarkSuite) -> None:
    """报告序列化保留六项判定与失败项（FAIL 报告可定位失守项）。"""
    report = evaluate_gates(suite, compute_metrics(**{**_PASSING, "p0_p1_blocked": 13}))
    payload = report.to_dict()
    assert payload["passed"] is False
    assert len(payload["gates"]) == 6
    assert "p0_p1_block_rate" in payload["failures"]


def test_operators_are_frozen_constants() -> None:
    """门禁运算符常量为 SPEC 固定值。"""
    assert OP_EQ == "eq"
    assert OP_GE == "ge"
    assert OP_LE == "le"


def test_compute_p95_empty_and_single() -> None:
    """P95 边界：空样本 0.0；单样本即自身。"""
    assert compute_p95([]) == 0.0
    assert compute_p95([12.5]) == 12.5


def test_compute_p95_nearest_rank() -> None:
    """P95 最近秩法：20 样本时取第 19 个（索引 18）。"""
    samples = [float(i) for i in range(1, 21)]
    assert compute_p95(samples) == 19.0


def test_compute_p95_monotone_inspread() -> None:
    """样本整体抬升 → P95 抬升（门禁能感知性能回归）。"""
    low = compute_p95([float(i) for i in range(1, 21)])
    high = compute_p95([float(i) * 2 for i in range(1, 21)])
    assert high > low
