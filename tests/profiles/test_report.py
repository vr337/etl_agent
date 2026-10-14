"""分级报告 SHA256 测试（T-07.6 / FR-15，S4 提示词第十节）。

覆盖：同输入同摘要（稳定性）、输入变化即摘要变化、最高级汇总、确定性序列化。
"""
from __future__ import annotations

from typing import Any

from etl_agent.profiles.report import build_report, canonical_json, sha256_of


def _columns(level: str = "INTERNAL") -> list[dict[str, Any]]:
    return [
        {
            "name": "order_id",
            "type": "integer",
            "nullable": False,
            "is_primary_key": True,
            "classification": "PUBLIC",
        },
        {
            "name": "customer_email",
            "type": "text",
            "nullable": True,
            "is_primary_key": False,
            "classification": level,
        },
    ]


def _build(level: str = "INTERNAL"):
    return build_report(
        endpoint_role="SOURCE",
        schema_name="public",
        table_name="orders",
        columns=_columns(level),
    )


def test_report_sha256() -> None:
    """同输入同输出：SHA256 稳定（FR-15 / S4 提示词第十节）。"""
    first = _build()
    second = _build()
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64
    assert all(character in "0123456789abcdef" for character in first.sha256)


def test_report_sha256_changes_with_input() -> None:
    """分级变化 → 摘要变化（否则报告无法作为可比对证据）。"""
    assert _build("INTERNAL").sha256 != _build("SECRET").sha256


def test_report_sha256_ignores_key_order() -> None:
    """确定性序列化：字典键序不影响摘要（同输入同摘要的必要条件）。"""
    left = {"a": 1, "b": [{"x": 1, "y": 2}]}
    right = {"b": [{"y": 2, "x": 1}], "a": 1}
    assert canonical_json(left) == canonical_json(right)
    assert sha256_of(left) == sha256_of(right)


def test_report_highest_classification() -> None:
    """报告汇总最高级：UNKNOWN 按保守等级参与（FR-15）。"""
    report = _build("UNKNOWN")
    assert report.payload["highest_classification"] == "CONFIDENTIAL"
    assert report.payload["columns"][1]["effective_classification"] == "CONFIDENTIAL"


def test_report_excludes_sample_values() -> None:
    """报告载荷只含元数据：不含任何样本值字段（AC-02）。"""
    payload = _build().payload
    assert "masked_samples" not in payload
    for column in payload["columns"]:
        assert set(column) == {
            "name",
            "type",
            "nullable",
            "is_primary_key",
            "classification",
            "effective_classification",
        }
