"""M-M 评测与安全进化 — Benchmark 分层评测器（L0 / L1 / L2）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（L0 静态注入 / L1 模拟故障 / L2 真实链路）
对应 AC：AC-14（L0）、AC-15（L1）、AC-16（L2）

`build_evaluators` 按层装配；L0 需要已验签的 PDP（由调用方注入，测试不新增密钥材料）。
"""

from etl_agent.benchmark.layers.base import (
    GATE_KEYS,
    LAYERS,
    BenchmarkSuite,
    Evaluator,
    LayerResult,
    Scenario,
    compute_manifest_digest,
    load_suite,
)
from etl_agent.benchmark.layers.l0_static import L0StaticEvaluator
from etl_agent.benchmark.layers.l1_simulation import L1SimulationEvaluator
from etl_agent.benchmark.layers.l2_e2e import L2E2EEvaluator

__all__ = [
    "GATE_KEYS",
    "LAYERS",
    "BenchmarkSuite",
    "Evaluator",
    "L0StaticEvaluator",
    "L1SimulationEvaluator",
    "L2E2EEvaluator",
    "LayerResult",
    "Scenario",
    "compute_manifest_digest",
    "load_suite",
]
