"""M-D 元数据探查与数据分级 — 分级报告（带 SHA256 摘要）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-D / 第 5.2 章 data_profiles.classification_report_sha256
覆盖 FR：FR-15（字段级 6 级分级 + 产出带 SHA256 的分级报告）
对应 AC：AC-02（报告只含列名 / 类型 / 分级，绝不含样本值）

**SHA256 稳定性**：摘要只覆盖确定性载荷 —— 端点角色、库表名、列名、类型、
可空性、主键标记与分级；不含时间戳、近似行数、采样值等随请求变化的事实。
同一输入必得同一摘要，使分级结论可作为可复现证据比对（与 FR-26 的
「不可变制品 + SHA256 冻结」同源思路）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from etl_agent.profiles.classification import effective_level, highest_classification

# 参与摘要的列字段（顺序即 JSON 键序，sort_keys 后由键名决定，此处仅作可读说明）
_REPORT_COLUMN_KEYS = ("name", "type", "nullable", "is_primary_key", "classification")


@dataclass(frozen=True)
class ClassificationReport:
    """分级报告：确定性载荷 + 其 SHA256（64 位十六进制小写）。"""

    payload: dict[str, Any]
    sha256: str


def canonical_json(payload: dict[str, Any]) -> str:
    """确定性 JSON 序列化（键排序 + 紧凑分隔符 + 不转义非 ASCII）。

    键排序消除字典插入顺序差异；紧凑分隔符消除空白差异 —— 两者都是
    「同输入同摘要」的必要条件。
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_of(payload: dict[str, Any]) -> str:
    """计算载荷的 SHA256（64 位十六进制小写）。"""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_report(
    *,
    endpoint_role: str,
    schema_name: str,
    table_name: str,
    columns: list[dict[str, Any]],
) -> ClassificationReport:
    """构造分级报告并计算 SHA256（FR-15）。

    Args:
        endpoint_role: SOURCE / SINK。
        schema_name / table_name: 受探查的源表。
        columns: 列字典列表（至少含 name / type / nullable / is_primary_key / classification）。

    Returns:
        ClassificationReport: 载荷（可对外返回）+ SHA256（落 `classification_report_sha256`）。
    """
    normalized: list[dict[str, Any]] = []
    for column in columns:
        classification = str(column["classification"])
        normalized.append(
            {
                "name": column["name"],
                "type": column["type"],
                "nullable": bool(column["nullable"]),
                "is_primary_key": bool(column["is_primary_key"]),
                "classification": classification,
                # UNKNOWN 的保守口径显式入摘要：策略侧按 CONFIDENTIAL 参与（FR-15）
                "effective_classification": effective_level(classification),
            }
        )
    payload: dict[str, Any] = {
        "endpoint_role": endpoint_role,
        "schema_name": schema_name,
        "table_name": table_name,
        "columns": normalized,
        "highest_classification": highest_classification(
            [entry["classification"] for entry in normalized]
        ),
    }
    return ClassificationReport(payload=payload, sha256=sha256_of(payload))
