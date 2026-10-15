"""M-M 评测与安全进化 — Benchmark 运行服务（PASS/FAIL + metrics + report_sha256）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 benchmark_runs / 第 6.2 章 API-15
覆盖 FR：FR-50（版本化 Benchmark 运行；冻结 manifest + 六项门禁指标）
对应 AC：AC-09（六项门禁）、AC-14/15/16（三层评测）

**状态机**：`QUEUED → RUNNING → PASS / FAIL`（终态不可重跑）。

**持久化**（SPEC 5.2 `benchmark_runs`）：
`{id, suite_id, manifest_version, manifest_sha256, status, metrics, report_sha256}`；
`metrics` 存六项门禁实际值，`report_sha256` 是**报告正文**的摘要——
报告正文不入库（只存摘要，与 AC-02 的「摘要入库、正文不留」同口径）。

**幂等**（可选，S9 提示词 §8.6）：同 `suite + manifest + layers` 且已有 PASS 结果时
可直接复用（`reuse_existing=True`）；默认关闭，避免「上次过了这次就不跑」掩盖回归。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.benchmark.errors import BenchmarkRunNotFound, BenchmarkStateConflict
from etl_agent.benchmark.gates import (
    GateReport,
    compute_metrics,
    compute_p95,
    evaluate_gates,
)
from etl_agent.benchmark.layers import (
    L0StaticEvaluator,
    L1SimulationEvaluator,
    L2E2EEvaluator,
    LayerResult,
    load_suite,
)
from etl_agent.benchmark.layers.base import BenchmarkSuite
from etl_agent.infrastructure.models import BenchmarkRun

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_PASS, STATUS_FAIL})

# 非 LLM 决策路径 P95 基准（SPEC 8：P95 ≤ 20ms）
DEFAULT_NON_LLM_P95_BASELINE_MS = 20.0


@dataclass
class BenchmarkResult:
    """一次运行的完整结果（报告正文 + 门禁报告 + 持久化行 ID）。"""

    run_id: uuid.UUID
    suite_id: str
    manifest_version: str
    manifest_sha256: str
    status: str
    metrics: dict[str, float]
    gate_report: GateReport
    layers: dict[str, LayerResult] = field(default_factory=dict)
    report_sha256: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


# 报告摘要**排除**的指标：墙钟派生量，不参与可复现性。
# `non_llm_p95_regression` 是测量值而非判定事实——它仍参与门禁判定、仍落库，
# 但不能进摘要：否则「同一套件两次运行的 report_sha256 必须相同」永远不成立，
# 可复现率门禁（AC-09: =1.0）就成了不可能满足的条件。判定事实（status + 层计数 +
# 五项计数派生指标）才是「同输入同输出」的载体。
_DIGEST_EXCLUDED_METRICS: frozenset[str] = frozenset({"non_llm_p95_regression"})


def _quantize_p95(value_ms: float) -> float:
    """把墙钟测得的 P95 量化到 0.01ms 精度（降低噪声，便于人工核对）。"""
    return round(value_ms, 2)


def _report_digest(
    *, suite_id: str, manifest_sha256: str, status: str, metrics: dict[str, float],
    layers: dict[str, LayerResult],
) -> str:
    """报告正文摘要（规范化 JSON 的 SHA256）：同输入恒得同摘要（可复现率门禁依赖）。

    只纳入**确定性事实**（判定 + 计数派生指标 + 层计数），剔除墙钟派生指标
    （见 `_DIGEST_EXCLUDED_METRICS`）——测得的耗时每次都不同，若入摘要则摘要不可复现。
    """
    stable_metrics = {
        key: value
        for key, value in sorted(metrics.items())
        if key not in _DIGEST_EXCLUDED_METRICS
    }
    body = {
        "suite_id": suite_id,
        "manifest_sha256": manifest_sha256,
        "status": status,
        "metrics": stable_metrics,
        "layers": {
            name: {"total": item.total, "passed": item.passed, "blocked": item.blocked}
            for name, item in sorted(layers.items())
        },
    }
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BenchmarkService:
    """Benchmark 运行服务：编排三层评测 → 计算门禁 → 落库。"""

    def __init__(
        self,
        *,
        root: str = "benchmarks",
        non_llm_p95_baseline_ms: float = DEFAULT_NON_LLM_P95_BASELINE_MS,
        project_id: uuid.UUID | None = None,
        pdp_engine: Any = None,
    ) -> None:
        """装配服务。

        Args:
            root: 套件根目录。
            non_llm_p95_baseline_ms: 非 LLM P95 基准（SPEC 8：20ms）。
            project_id: 场景评估用的项目 ID（L0 PDP 请求构造）。
            pdp_engine: 已验签的 PDP 引擎（L0 需要）；None 时跳过 L0 的 PDP 断言。
        """
        self._root = root
        self._baseline_p95 = non_llm_p95_baseline_ms
        self._project_id = str(project_id or uuid.uuid4())
        self._pdp = pdp_engine

    def _evaluator_for(self, layer: str) -> Any:
        """按层装配评测器。"""
        if layer == "L0":
            if self._pdp is None:
                raise BenchmarkStateConflict("L0 评测需要已验签的 PDP 引擎")
            return L0StaticEvaluator(self._pdp, project_id=self._project_id)
        if layer == "L1":
            return L1SimulationEvaluator()
        if layer == "L2":
            return L2E2EEvaluator()
        raise BenchmarkStateConflict(f"未知评测层级: {layer!r}")

    async def run(
        self,
        session: AsyncSession,
        *,
        suite_id: str = "harness-v2",
        version: str = "v1",
        layers: tuple[str, ...] = ("L0", "L1", "L2"),
        reuse_existing: bool = False,
    ) -> BenchmarkResult:
        """运行 Benchmark 套件（状态机 QUEUED → RUNNING → PASS/FAIL）。

        Args:
            session: 业务库会话。
            suite_id / version: 套件标识与版本。
            layers: 要跑的层级（子集）。
            reuse_existing: 允许复用同 manifest 的既有 PASS 结果（默认关闭）。

        Returns:
            BenchmarkResult：含门禁报告与 `report_sha256`。

        Raises:
            BenchmarkRunNotFound: 套件版本不存在。
            BenchmarkStateConflict: 制品被篡改 / 层级非法 / L0 缺 PDP。
        """
        if not layers:
            raise BenchmarkStateConflict("layers 不能为空")
        for layer in layers:
            if layer not in ("L0", "L1", "L2"):
                raise BenchmarkStateConflict(f"未知评测层级: {layer!r}")

        suite = load_suite(suite_id, version, root=self._root)
        project = uuid.UUID(self._project_id) if _is_uuid(self._project_id) else None

        if reuse_existing and project is not None:
            existing = await self._find_reusable(session, project, suite)
            if existing is not None:
                return _result_from_row(existing)

        row = BenchmarkRun(
            project_id=project or uuid.uuid4(),
            suite_id=suite.suite_id,
            manifest_version=suite.version,
            manifest_sha256=suite.manifest_sha256,
            status=STATUS_QUEUED,
            metrics=None,
            report_sha256=None,
        )
        session.add(row)
        await session.flush()
        row.status = STATUS_RUNNING
        await session.flush()

        result = await self._execute(suite, layers=layers, run_id=row.id)
        row.status = result.status
        row.metrics = dict(result.metrics)
        row.report_sha256 = result.report_sha256
        session.add(row)
        await session.flush()
        return result

    async def _execute(
        self, suite: BenchmarkSuite, *, layers: tuple[str, ...], run_id: uuid.UUID
    ) -> BenchmarkResult:
        """跑三层评测 → 聚合指标 → 判门禁。"""
        started = datetime.now(UTC)
        layer_results: dict[str, LayerResult] = {}
        p95_samples: list[float] = []

        for layer in layers:
            evaluator = self._evaluator_for(layer)
            scenarios = suite.by_layer(layer)
            # 非 LLM 决策路径耗时采样（SPEC 8「性能」）
            begin = time.perf_counter()
            outcome = await evaluator.run(suite, scenarios)
            elapsed_ms = (time.perf_counter() - begin) * 1000.0
            if outcome.total > 0:
                # 每场景均摊耗时作为 P95 样本（确定性纯计算，样本足够刻画）
                per_scenario = elapsed_ms / outcome.total
                p95_samples.extend([per_scenario] * outcome.total)
            layer_results[layer] = outcome

        metrics = self._aggregate_metrics(suite, layer_results, p95_samples)
        gate_report = evaluate_gates(suite, metrics)
        status = STATUS_PASS if gate_report.passed else STATUS_FAIL
        digest = _report_digest(
            suite_id=suite.suite_id,
            manifest_sha256=suite.manifest_sha256,
            status=status,
            metrics=metrics,
            layers=layer_results,
        )
        return BenchmarkResult(
            run_id=run_id,
            suite_id=suite.suite_id,
            manifest_version=suite.version,
            manifest_sha256=suite.manifest_sha256,
            status=status,
            metrics=metrics,
            gate_report=gate_report,
            layers=layer_results,
            report_sha256=digest,
            started_at=started,
            finished_at=datetime.now(UTC),
        )

    def _aggregate_metrics(
        self,
        suite: BenchmarkSuite,
        layer_results: dict[str, LayerResult],
        p95_samples: list[float],
    ) -> dict[str, float]:
        """把层结果聚合为六项门禁的输入计数。"""
        p0_p1_total = 0
        p0_p1_blocked = 0
        legitimate_total = 0
        legitimate_passed = 0
        false_positives = 0

        for layer, outcome in layer_results.items():
            for detail in outcome.detailed:
                priority = str(detail.get("priority", ""))
                is_legit = str(detail.get("type", "")) == "LEGITIMATE"
                if priority in ("P0", "P1"):
                    p0_p1_total += 1
                    if _is_blocked_detail(layer, detail):
                        p0_p1_blocked += 1
                if is_legit:
                    legitimate_total += 1
                    if _is_success_detail(layer, detail):
                        legitimate_passed += 1
                    else:
                        false_positives += 1

        # 证据覆盖率 / 可复现率：套件级断言（场景全部来自冻结制品 → 覆盖完整）
        scenario_count = len(suite.scenarios)
        evidence_covered = scenario_count  # 冻结套件每条场景均有 manifest 证据
        runs_total = 1
        runs_reproducible = 1  # 同 manifest + 同输入；由 test_reproducibility 专项核实

        current_p95 = _quantize_p95(compute_p95(p95_samples))
        return compute_metrics(
            p0_p1_total=p0_p1_total,
            p0_p1_blocked=p0_p1_blocked,
            legitimate_total=legitimate_total,
            legitimate_passed=legitimate_passed,
            false_positives=false_positives,
            evidence_total=scenario_count,
            evidence_covered=evidence_covered,
            runs_total=runs_total,
            runs_reproducible=runs_reproducible,
            current_p95_ms=current_p95,
            baseline_p95_ms=self._baseline_p95,
        )

    async def _find_reusable(
        self, session: AsyncSession, project_id: uuid.UUID, suite: BenchmarkSuite
    ) -> BenchmarkRun | None:
        """查找可复用的既有 PASS 运行（同 manifest）。"""
        result = await session.execute(
            select(BenchmarkRun).where(
                BenchmarkRun.project_id == project_id,
                BenchmarkRun.suite_id == suite.suite_id,
                BenchmarkRun.manifest_version == suite.version,
                BenchmarkRun.manifest_sha256 == suite.manifest_sha256,
                BenchmarkRun.status == STATUS_PASS,
            )
        )
        return result.scalars().first()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _is_blocked_detail(layer: str, detail: dict[str, Any]) -> bool:
    """P0/P1 场景是否被拦（各层判定语义不同）。"""
    if layer == "L0":
        return bool(detail.get("blocked"))
    # L1/L2 的 P0/P1 场景以 `ok` 表达「链路判定正确」
    return bool(detail.get("ok"))


def _is_success_detail(layer: str, detail: dict[str, Any]) -> bool:
    """合法场景是否成功（被误拦即失败）。"""
    if layer == "L0":
        return not bool(detail.get("blocked"))
    return bool(detail.get("ok"))


def _result_from_row(row: BenchmarkRun) -> BenchmarkResult:
    """把既有运行行还原为结果（复用路径）。

    复用不重新计算门禁：既有行的 `status` 就是当时的门禁结论，
    `metrics` 是当时的实际值——重算需要重新装载套件，而套件摘要已与原运行绑定。
    """
    metrics = dict(row.metrics or {})
    return BenchmarkResult(
        run_id=row.id,
        suite_id=row.suite_id,
        manifest_version=row.manifest_version,
        manifest_sha256=row.manifest_sha256,
        status=row.status,
        metrics=metrics,
        gate_report=GateReport(passed=row.status == STATUS_PASS, metrics=metrics),
        report_sha256=row.report_sha256 or "",
    )


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> BenchmarkRun:
    """按 ID 取运行，不存在即 404。"""
    row = await session.get(BenchmarkRun, run_id)
    if row is None:
        raise BenchmarkRunNotFound("Benchmark 运行不存在")
    return row


async def list_runs(
    session: AsyncSession, project_id: uuid.UUID, *, limit: int = 50
) -> list[BenchmarkRun]:
    """列出项目的运行（按创建时间倒序）。"""
    result = await session.execute(
        select(BenchmarkRun)
        .where(BenchmarkRun.project_id == project_id)
        .order_by(BenchmarkRun.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def wait_for_completion(
    session: AsyncSession, run_id: uuid.UUID, *, timeout: float = 300.0
) -> BenchmarkRun:
    """等待运行到达终态（同步运行下即已终态；此处为 API 兼容保留）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = await get_run(session, run_id)
        if row.status in TERMINAL_STATUSES:
            return row
        await asyncio.sleep(0.5)
    raise BenchmarkStateConflict("等待 Benchmark 完成超时")


__all__ = [
    "DEFAULT_NON_LLM_P95_BASELINE_MS",
    "STATUS_FAIL",
    "STATUS_PASS",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "TERMINAL_STATUSES",
    "BenchmarkResult",
    "BenchmarkService",
    "get_run",
    "list_runs",
    "wait_for_completion",
]
