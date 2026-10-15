"""M-M 评测与安全进化 — Benchmark 分层评测基类与套件装载。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（L0/L1/L2 分层仿真评测 + 冻结 manifest + baseline）
对应 AC：AC-09（六项门禁）、AC-14/15/16（三层各自验收）

**只读装载**：`load_suite` 读 `benchmarks/harness-v2/v1/` 三份冻结制品，
并**强制校验 manifest 中的 SHA256**——制品被改动即拒绝运行（S9 禁止事项 15：
禁止篡改 manifest / 防「自我美化」）。校验失败抛 `BenchmarkStateConflict`，
不降级为「按当前内容跑」。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from etl_agent.benchmark.errors import BenchmarkRunNotFound, BenchmarkStateConflict

# 六项门禁键（SPEC FR-50，顺序即报告顺序）
GATE_KEYS: tuple[str, ...] = (
    "p0_p1_block_rate",
    "legitimate_success_rate",
    "false_positive_rate",
    "evidence_coverage_rate",
    "reproducibility_rate",
    "non_llm_p95_regression",
)

# 评测层级
LAYERS: tuple[str, ...] = ("L0", "L1", "L2")

# 套件根目录（相对仓库根）
DEFAULT_SUITE_ROOT = "benchmarks"


@dataclass(frozen=True)
class Scenario:
    """单个评测场景（来自冻结的 scenarios.json）。"""

    id: str
    layer: str
    priority: str
    type: str
    name: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking_priority(self) -> bool:
        """是否属 P0/P1（拦截率门禁的分子分母口径）。"""
        return self.priority in ("P0", "P1")

    @property
    def is_legitimate(self) -> bool:
        """是否合法场景（合法成功率 / 误报率门禁口径）。"""
        return self.type == "LEGITIMATE"


@dataclass(frozen=True)
class BenchmarkSuite:
    """冻结的 Benchmark 套件（场景 + baseline + manifest 摘要）。"""

    suite_id: str
    version: str
    manifest_sha256: str
    manifest: dict[str, Any]
    baseline: dict[str, Any]
    scenarios: tuple[Scenario, ...]

    def by_layer(self, layer: str) -> tuple[Scenario, ...]:
        return tuple(item for item in self.scenarios if item.layer == layer)

    def gate_threshold(self, key: str) -> tuple[str, float]:
        """取门禁 `(operator, threshold)`；未登记即配置错误。"""
        gates = self.baseline.get("gates", {})
        entry = gates.get(key)
        if not isinstance(entry, dict):
            raise BenchmarkStateConflict(f"baseline 缺少门禁定义: {key}")
        return str(entry.get("operator", "eq")), float(entry.get("threshold", 0.0))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_manifest_digest(manifest: dict[str, Any]) -> str:
    """manifest 自校验摘要（去掉 `manifest_sha256` 字段后规范化 JSON 的 SHA256）。"""
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_suite(
    suite_id: str = "harness-v2",
    version: str = "v1",
    *,
    root: str | Path = DEFAULT_SUITE_ROOT,
) -> BenchmarkSuite:
    """装载并校验冻结套件。

    Args:
        suite_id: 套件标识（如 `harness-v2`）。
        version: 冻结版本（如 `v1`）。
        root: 套件根目录（默认 `benchmarks`，相对当前工作目录）。

    Returns:
        BenchmarkSuite：已验签（SHA256 对账）的场景、baseline 与 manifest。

    Raises:
        BenchmarkRunNotFound: 套件版本目录或制品缺失。
        BenchmarkStateConflict: manifest 自校验失败，或制品 SHA256 与冻结值不符
            （说明套件被改动，禁止运行——S9 禁止事项 15）。
    """
    directory = Path(root) / suite_id / version
    if not directory.is_dir():
        raise BenchmarkRunNotFound(f"Benchmark 套件版本不存在: {suite_id}/{version}")

    manifest_path = directory / "manifest.json"
    scenarios_path = directory / "scenarios.json"
    baseline_path = directory / "baseline.json"
    for path in (manifest_path, scenarios_path, baseline_path):
        if not path.is_file():
            raise BenchmarkRunNotFound(f"套件制品缺失: {path.name}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 1. manifest 自校验：防清单本身被改
    expected_self = compute_manifest_digest(manifest)
    if manifest.get("manifest_sha256") != expected_self:
        raise BenchmarkStateConflict(
            "Benchmark manifest 自校验失败：清单被篡改，拒绝运行（禁止事项 15）"
        )

    # 2. 制品 SHA256 对账：防场景 / 阈值被改（「自我美化」）
    artifacts = manifest.get("artifacts", {})
    for name, path in (("scenarios.json", scenarios_path), ("baseline.json", baseline_path)):
        frozen = artifacts.get(name, {})
        expected = frozen.get("sha256") if isinstance(frozen, dict) else None
        actual = _sha256_file(path)
        if expected != actual:
            raise BenchmarkStateConflict(
                f"Benchmark 制品 {name} 的 SHA256 与冻结 manifest 不符，拒绝运行（禁止事项 15）"
            )

    scenarios_doc = json.loads(scenarios_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    scenarios = tuple(
        Scenario(
            id=str(item["id"]),
            layer=str(item["layer"]),
            priority=str(item["priority"]),
            type=str(item["type"]),
            name=str(item.get("name", item["id"])),
            payload=item,
        )
        for item in scenarios_doc.get("scenarios", [])
    )
    return BenchmarkSuite(
        suite_id=suite_id,
        version=version,
        manifest_sha256=str(manifest.get("manifest_sha256", "")),
        manifest=manifest,
        baseline=baseline,
        scenarios=scenarios,
    )


@dataclass
class LayerResult:
    """单层评测结果（供门禁计算聚合）。"""

    layer: str
    total: int = 0
    blocked: int = 0
    passed: int = 0
    false_positives: int = 0
    detailed: list[dict[str, Any]] = field(default_factory=list)

    @property
    def all_blocked(self) -> bool:
        return self.total == self.blocked


@runtime_checkable
class Evaluator(Protocol):
    """分层评测器契约（L0/L1/L2 各自实现）。"""

    layer: str

    async def run(self, suite: BenchmarkSuite, scenarios: tuple[Scenario, ...]) -> LayerResult:
        """执行该层场景，返回层结果。"""
        ...


__all__ = [
    "DEFAULT_SUITE_ROOT",
    "GATE_KEYS",
    "LAYERS",
    "BenchmarkSuite",
    "Evaluator",
    "LayerResult",
    "Scenario",
    "compute_manifest_digest",
    "load_suite",
]
