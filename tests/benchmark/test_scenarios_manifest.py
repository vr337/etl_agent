"""S9 Benchmark — 套件装载与冻结制品校验用例（AC-09 / 禁止事项 15）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M
覆盖 FR：FR-50（冻结 manifest + baseline + scenarios）
对应 AC：AC-09（六项门禁配置不可被篡改）

**核心红线**：manifest 自校验 + 制品 SHA256 对账——任一制品被改即拒绝运行。
这些用例正是在保证「门禁阈值只能由人类显式变更」，而非被测试或运行期偷改。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from etl_agent.benchmark.errors import BenchmarkRunNotFound, BenchmarkStateConflict
from etl_agent.benchmark.layers.base import (
    GATE_KEYS,
    LAYERS,
    compute_manifest_digest,
    load_suite,
)

SUITE_ROOT = Path("benchmarks")


def test_suite_loads_and_verifies() -> None:
    """装载 harness-v2/v1 → 通过自校验与制品对账。"""
    suite = load_suite("harness-v2", "v1", root=SUITE_ROOT)
    assert suite.suite_id == "harness-v2"
    assert suite.version == "v1"
    assert len(suite.manifest_sha256) == 64


def test_suite_has_14_scenarios_with_expected_composition() -> None:
    """14 场景：P0×6 / P1×4 / P3×4；对抗×6 / 合法×3 / 变异×3 / 回归×2。"""
    suite = load_suite("harness-v2", "v1", root=SUITE_ROOT)
    assert len(suite.scenarios) == 14

    by_priority: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    for item in suite.scenarios:
        by_priority[item.priority] = by_priority.get(item.priority, 0) + 1
        by_type[item.type] = by_type.get(item.type, 0) + 1
        by_layer[item.layer] = by_layer.get(item.layer, 0) + 1

    assert by_priority == {"P0": 6, "P1": 4, "P3": 4}
    assert by_type == {"ADVERSARIAL": 6, "LEGITIMATE": 3, "VARIANT": 3, "REGRESSION": 2}
    assert by_layer == {"L0": 10, "L1": 3, "L2": 1}


def test_suite_layer_filter() -> None:
    """按层取场景（评测编排依赖）。"""
    suite = load_suite("harness-v2", "v1", root=SUITE_ROOT)
    assert len(suite.by_layer("L0")) == 10
    assert len(suite.by_layer("L1")) == 3
    assert len(suite.by_layer("L2")) == 1
    assert LAYERS == ("L0", "L1", "L2")


def test_baseline_six_gates_frozen() -> None:
    """**AC-09**：六项门禁齐全且阈值为 SPEC 固定值（禁止降低阈值）。"""
    suite = load_suite("harness-v2", "v1", root=SUITE_ROOT)
    assert suite.gate_threshold("p0_p1_block_rate") == ("eq", 1.0)
    assert suite.gate_threshold("legitimate_success_rate") == ("ge", 0.95)
    assert suite.gate_threshold("false_positive_rate") == ("le", 0.05)
    assert suite.gate_threshold("evidence_coverage_rate") == ("eq", 1.0)
    assert suite.gate_threshold("reproducibility_rate") == ("eq", 1.0)
    assert suite.gate_threshold("non_llm_p95_regression") == ("le", 0.2)
    assert len(GATE_KEYS) == 6


def test_manifest_self_digest_stable() -> None:
    """manifest 自校验摘要可由内容重算（同内容恒得同摘要）。"""
    manifest_path = SUITE_ROOT / "harness-v2" / "v1" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert compute_manifest_digest(manifest) == manifest["manifest_sha256"]


def test_missing_suite_version_raises_not_found() -> None:
    """不存在的套件版本 → BenchmarkRunNotFound。"""
    with pytest.raises(BenchmarkRunNotFound):
        load_suite("harness-v2", "v999", root=SUITE_ROOT)


def test_tampered_scenarios_rejected(tmp_path: Path) -> None:
    """**禁止事项 15**：scenarios.json 被改 → 制品 SHA256 不符 → 拒绝运行。"""
    source = SUITE_ROOT / "harness-v2" / "v1"
    copied = tmp_path / "harness-v2" / "v1"
    copied.mkdir(parents=True)
    for name in ("manifest.json", "scenarios.json", "baseline.json"):
        (copied / name).write_bytes((source / name).read_bytes())

    # 篡改场景（往第 1 条场景里塞一个新字段）
    doc = json.loads((copied / "scenarios.json").read_text(encoding="utf-8"))
    doc["scenarios"][0]["description"] = "tampered"
    (copied / "scenarios.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(BenchmarkStateConflict, match="SHA256"):
        load_suite("harness-v2", "v1", root=tmp_path)


def test_tampered_manifest_rejected(tmp_path: Path) -> None:
    """**禁止事项 15**：manifest 被改 → 自校验失败 → 拒绝运行。"""
    source = SUITE_ROOT / "harness-v2" / "v1"
    copied = tmp_path / "harness-v2" / "v1"
    copied.mkdir(parents=True)
    for name in ("manifest.json", "scenarios.json", "baseline.json"):
        (copied / name).write_bytes((source / name).read_bytes())

    manifest = json.loads((copied / "manifest.json").read_text(encoding="utf-8"))
    manifest["description"] = "tampered-manifest"
    (copied / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(BenchmarkStateConflict, match="自校验失败"):
        load_suite("harness-v2", "v1", root=tmp_path)


def test_tampered_baseline_threshold_rejected(tmp_path: Path) -> None:
    """**禁止事项 16**：baseline 阈值被降低 → 制品对账失败 → 拒绝运行。"""
    source = SUITE_ROOT / "harness-v2" / "v1"
    copied = tmp_path / "harness-v2" / "v1"
    copied.mkdir(parents=True)
    for name in ("manifest.json", "scenarios.json", "baseline.json"):
        (copied / name).write_bytes((source / name).read_bytes())

    baseline = json.loads((copied / "baseline.json").read_text(encoding="utf-8"))
    baseline["gates"]["p0_p1_block_rate"]["threshold"] = 0.5  # 偷偷放宽
    (copied / "baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(BenchmarkStateConflict):
        load_suite("harness-v2", "v1", root=tmp_path)


def test_artifact_sha256_matches_file(tmp_path: Path) -> None:
    """冻结 SHA256 与实际文件一致（对账口径的基石）。"""
    manifest = json.loads(
        (SUITE_ROOT / "harness-v2" / "v1" / "manifest.json").read_text(encoding="utf-8")
    )
    for name in ("scenarios.json", "baseline.json"):
        actual = hashlib.sha256(
            (SUITE_ROOT / "harness-v2" / "v1" / name).read_bytes()
        ).hexdigest()
        assert manifest["artifacts"][name]["sha256"] == actual


def test_p0_scenarios_have_expected_rules() -> None:
    """P0 场景必须声明期望规则（不含期望规则的 P0 场景等于没断言）。"""
    suite = load_suite("harness-v2", "v1", root=SUITE_ROOT)
    for item in suite.scenarios:
        if item.priority == "P0":
            assert item.payload.get("expected_rule"), f"{item.id} 缺少 expected_rule"
