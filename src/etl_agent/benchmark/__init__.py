"""M-M 评测与安全进化 — 版本化 Benchmark 套件与分层评测（L0/L1/L2 + 六项门禁）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 benchmark_runs /
第 6.1 章 API-15 / 第 6.4 章错误码
覆盖 FR：FR-50（L0 静态注入 / L1 模拟故障 / L2 真实链路 + 冻结 manifest + baseline + 六项门禁）
对应 AC：AC-09（六项门禁）、AC-14（L0）、AC-15（L1）、AC-16（L2）

模块职责：运行服务（`service`）、门禁计算（`gates`）、分层评测（`layers`）、
CLI（`cli`）、API（`router` / `schemas`）、错误码（`errors`）。
"""

from etl_agent.benchmark.errors import (
    BenchmarkGateFailed,
    BenchmarkRunNotFound,
    BenchmarkStateConflict,
)
from etl_agent.benchmark.gates import GateOutcome, GateReport, compute_metrics, evaluate_gates
from etl_agent.benchmark.layers import (
    GATE_KEYS,
    LAYERS,
    BenchmarkSuite,
    L0StaticEvaluator,
    L1SimulationEvaluator,
    L2E2EEvaluator,
    LayerResult,
    Scenario,
    load_suite,
)
from etl_agent.benchmark.service import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_QUEUED,
    STATUS_RUNNING,
    BenchmarkResult,
    BenchmarkService,
    get_run,
    list_runs,
)

__all__ = [
    "GATE_KEYS",
    "LAYERS",
    "STATUS_FAIL",
    "STATUS_PASS",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "BenchmarkGateFailed",
    "BenchmarkResult",
    "BenchmarkRunNotFound",
    "BenchmarkService",
    "BenchmarkStateConflict",
    "BenchmarkSuite",
    "GateOutcome",
    "GateReport",
    "L0StaticEvaluator",
    "L1SimulationEvaluator",
    "L2E2EEvaluator",
    "LayerResult",
    "Scenario",
    "compute_metrics",
    "evaluate_gates",
    "get_run",
    "list_runs",
    "load_suite",
]
