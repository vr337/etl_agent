"""版本 Diff 测试（T-11.5；S6 提示词第十二节 tests/versions）。

覆盖（FR-27）：Schema / HOCON / 质量规则三类差异 + 摘要级差异；
纯函数无 IO，正文由调用方从 Checkpoint 取回（S5 吸收 2 决议）。
"""
from __future__ import annotations

from etl_agent.versions.diff import (
    diff_artifact_digests,
    diff_columns,
    diff_hocon,
    diff_plans,
    diff_quality_contract,
    has_changes,
)

PLAN_LEFT = {
    "source": {"connector_type": "POSTGRESQL"},
    "sink": {
        "connector_type": "DORIS",
        "target_schema": {
            "columns": [
                {"name": "id", "type": "bigint"},
                {"name": "amount", "type": "numeric"},
                {"name": "region", "type": "varchar"},
            ]
        },
    },
    "quality_contract": {
        "predicates": [{"name": "not_null_id", "expression": "id IS NOT NULL"}],
        "routing": [{"rule": "not_null_id", "sink": "valid"}],
    },
}

PLAN_RIGHT = {
    "source": {"connector_type": "MYSQL"},
    "sink": {
        "connector_type": "DORIS",
        "target_schema": {
            "columns": [
                {"name": "id", "type": "bigint"},
                {"name": "amount", "type": "decimal"},  # 类型变更
                {"name": "updated_at", "type": "timestamptz"},  # 新增
            ]
        },
    },
    "quality_contract": {
        "predicates": [
            {"name": "not_null_id", "expression": "id IS NOT NULL"},
            {"name": "positive_amount", "expression": "amount > 0"},  # 新增
        ],
        "routing": [{"rule": "not_null_id", "sink": "quarantine"}],  # sink 变更
    },
}


def test_diff_versions() -> None:
    """两版本差异：Schema 增删改 + 质量规则增改 + 摘要变化（FR-27）。"""
    diff = diff_plans(PLAN_LEFT, PLAN_RIGHT)

    schema = diff["schema"]
    assert schema["added"] == ["updated_at"]
    assert schema["removed"] == ["region"]
    assert schema["type_changed"] == [
        {"name": "amount", "before": "numeric", "after": "decimal"}
    ]

    quality = diff["quality_contract"]
    assert "predicate:positive_amount" in quality["added"]
    assert [item["rule"] for item in quality["changed"]] == ["routing:not_null_id"]
    assert has_changes(diff)


def test_diff_hocon() -> None:
    """HOCON 行级差异：增删行数与是否变化。"""
    before = 'env {\n  job.mode = "BATCH"\n}\nsink { Jdbc { } }\n'
    after = 'env {\n  job.mode = "STREAMING"\n}\nsink { Jdbc { } }\nsource { }\n'
    diff = diff_hocon(before, after)
    assert diff["changed"] is True
    # 行集差：job.mode 行（BATCH→STREAMING）+ source { } 为新增，job.mode BATCH 行为删除
    assert diff["added_lines"] == 2
    assert diff["removed_lines"] == 1
    assert diff["before_line_count"] == 4
    assert diff["after_line_count"] == 5
    assert diff_hocon(before, before)["changed"] is False


def test_diff_columns_missing_shapes() -> None:
    """非预期形态（空 plan / 缺 target_schema）不抛异常，返回空差异。"""
    assert diff_columns({}, {}) == {"added": [], "removed": [], "type_changed": []}
    assert diff_quality_contract({}, {}) == {"added": [], "removed": [], "changed": []}


def test_diff_artifact_digests() -> None:
    """摘要级差异：缺项标 `<缺失>`，逐类型比较（业务库即可给出，恒可用）。"""
    diff = diff_artifact_digests(
        {"plan": "a" * 64, "hocon": "b" * 64},
        {"plan": "a" * 64, "hocon": "c" * 64, "quality_contract": "d" * 64},
    )
    assert diff["plan"]["changed"] is False
    assert diff["hocon"]["changed"] is True
    assert diff["quality_contract"]["before"] == "<缺失>"
    assert has_changes(diff)
