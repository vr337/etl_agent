"""监督契约与 6 条规则注册表用例（T-16.1，FR-45）。

覆盖：6 条规则全注册、冻结预算「只收窄不放宽」、规则 ID 与契约一致。
"""
from __future__ import annotations

from etl_agent.supervision import contracts as contracts_module
from etl_agent.supervision.contracts import (
    DEFAULT_BUDGET,
    KEY_MAX_BYTES,
    KEY_MAX_RECORDS,
    KEY_MIN_QUALITY_ROWS,
    KEY_WARNING_RATIO,
    RULE_IDS,
    SupervisionContract,
)
from etl_agent.supervision.rules import ALL_RULES, REGISTERED_RULE_IDS


def test_6_rules_registered() -> None:
    """注册表恰有 6 条规则，且顺序与 SPEC 5.2 / 提示词 §8.4 一致。"""
    assert len(ALL_RULES) == 6
    assert REGISTERED_RULE_IDS == RULE_IDS
    assert len(set(RULE_IDS)) == 6


def test_rule_ids_exact() -> None:
    """规则 ID 逐条核对（防改名导致验收命令 9 落空）。"""
    assert RULE_IDS == (
        "SUPERVISION-P0-RECORD",
        "SUPERVISION-P0-BYTE",
        "SUPERVISION-P1-TIMEOUT",
        "SUPERVISION-P0-AMPLIFICATION",
        "SUPERVISION-P1-REJECT-RATE",
        "SUPERVISION-P3-WARNING",
    )


def test_all_rules_registered() -> None:
    """`all_rules()` 与注册表同源（延迟导入装配点唯一）。"""
    assert len(contracts_module.all_rules()) == 6


def test_frozen_budget_defaults() -> None:
    """SPEC 5.2 冻结预算默认值。"""
    contract = SupervisionContract.frozen()
    assert contract.max_records == 10_000_000
    assert contract.max_bytes == 10 * 1024**3
    assert contract.max_duration_seconds == 3600
    assert contract.max_amplification_ratio == 2.0
    assert contract.max_reject_rate == 0.5
    assert contract.min_quality_rows == 100
    assert contract.warning_ratio == 0.8
    assert contract.as_dict() == DEFAULT_BUDGET


def test_resolve_narrows_upper_bounds() -> None:
    """上界类项只收窄：传入更小值生效，传入更大值被夹回默认。"""
    narrowed = SupervisionContract.resolve({KEY_MAX_RECORDS: 500, KEY_MAX_BYTES: 1024})
    assert narrowed.max_records == 500
    assert narrowed.max_bytes == 1024
    widened = SupervisionContract.resolve(
        {KEY_MAX_RECORDS: 999_999_999, KEY_MAX_BYTES: 10**18}
    )
    assert widened.max_records == 10_000_000
    assert widened.max_bytes == 10 * 1024**3


def test_resolve_raises_lower_bound() -> None:
    """下界类项只收紧：`min_quality_rows` 传入更小值被抬回默认（禁止放宽）。"""
    contract = SupervisionContract.resolve({KEY_MIN_QUALITY_ROWS: 1})
    assert contract.min_quality_rows == 100
    stricter = SupervisionContract.resolve({KEY_MIN_QUALITY_ROWS: 500})
    assert stricter.min_quality_rows == 500


def test_resolve_ignores_illegal_types() -> None:
    """类型非法 / 布尔值一律回落默认（不把 True 当 1）。"""
    contract = SupervisionContract.resolve(
        {KEY_MAX_RECORDS: True, KEY_WARNING_RATIO: "0.5"}  # type: ignore[dict-item]
    )
    assert contract.max_records == 10_000_000
    assert contract.warning_ratio == 0.8


def test_resolve_none_returns_frozen() -> None:
    """无传入预算 → 冻结默认（不得因缺省而放宽）。"""
    assert SupervisionContract.resolve(None).as_dict() == DEFAULT_BUDGET
