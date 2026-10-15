"""原子 Swap 用例（T-15.x，AC-07 核心 / FR-43）。

覆盖：happy path（Schema 一致 → swap 执行）、Schema 不一致 → 拒绝（不改表结构）、
类型归一化、幂等记录、SQL 构造唯一性、生产通道走 MySQL 协议。
"""

from __future__ import annotations

import pytest

from etl_agent.execution.atomic_swap import (
    SWAP_SQL_TEMPLATE,
    DorisSwapExecutor,
    LoggingSwapExecutor,
    SwapExecutor,
    TableColumn,
    build_swap_sql,
    check_schema_alignment,
    compare_schemas,
    execute_atomic_swap,
    normalize_type,
)
from etl_agent.execution.errors import SwapSchemaMismatch


def test_swap_sql_template_shape() -> None:
    """Swap 语句是 Doris `REPLACE WITH TABLE ... PROPERTIES('swap'='true')`（SPEC 3.2 M-K）。"""
    assert build_swap_sql("orders_wide", "orders_wide_shadow") == (
        "ALTER TABLE orders_wide REPLACE WITH TABLE orders_wide_shadow PROPERTIES('swap'='true');"
    )
    assert "{target}" in SWAP_SQL_TEMPLATE and "{shadow}" in SWAP_SQL_TEMPLATE


def test_normalize_type_aliases() -> None:
    """类型归一化：别名归并、去参数、大小写不敏感。"""
    assert normalize_type("VARCHAR(255)") == "string"
    assert normalize_type("char(10)") == "string"
    assert normalize_type("DATETIME") == "timestamp"
    assert normalize_type("DECIMAL(10,2)") == "decimal"
    assert normalize_type("bigint") == "bigint"
    assert normalize_type("custom_type") == "custom_type"


def test_compare_schemas_aligned() -> None:
    """列名 + 类型一致 → aligned。"""
    target = [TableColumn("id", "bigint"), TableColumn("amount", "decimal(10,2)")]
    shadow = [TableColumn("ID", "BIGINT"), TableColumn("amount", "DECIMAL")]
    result = compare_schemas(target, shadow)
    assert result.aligned is True
    assert result.summary() == "Schema 一致"


def test_compare_schemas_reports_differences() -> None:
    """缺列 / 多列 / 类型不一致逐项列出（摘要不含数据，AC-02）。"""
    target = [TableColumn("id", "bigint"), TableColumn("amount", "decimal")]
    shadow = [TableColumn("id", "string"), TableColumn("extra", "int")]
    result = compare_schemas(target, shadow)
    assert result.aligned is False
    assert "amount" in result.missing_in_shadow
    assert "extra" in result.missing_in_target
    assert any("id" in item for item in result.type_mismatches)
    assert "Schema" not in result.summary() or "不一致" in result.summary()


async def test_atomic_swap_success() -> None:
    """**AC-07 核心**：Schema 一致 → Swap 执行并留痕。"""
    executor = LoggingSwapExecutor()
    result = await execute_atomic_swap(
        executor, target_table="orders_wide", shadow_table="orders_wide_shadow"
    )
    assert result.executed_sql == build_swap_sql("orders_wide", "orders_wide_shadow")
    assert executor.swapped == [("orders_wide", "orders_wide_shadow")]
    assert executor.executed == [result.executed_sql]
    assert result.swapped_at is not None


async def test_atomic_swap_schema_mismatch() -> None:
    """Schema 不一致 → 拒绝 Swap（宁可发布失败也不静默改表结构，FR-43）。"""
    executor = LoggingSwapExecutor(
        schemas={
            "orders_wide": [TableColumn("id", "bigint")],
            "orders_wide_shadow": [TableColumn("id", "string")],
        }
    )
    with pytest.raises(SwapSchemaMismatch):
        await execute_atomic_swap(
            executor, target_table="orders_wide", shadow_table="orders_wide_shadow"
        )
    assert executor.swapped == []  # 未执行任何 swap


async def test_check_schema_alignment() -> None:
    """读取两侧 Schema 并比对（对齐 → aligned）。"""
    executor = LoggingSwapExecutor()
    comparison = await check_schema_alignment(executor, target_table="t", shadow_table="t_shadow")
    assert comparison.aligned is True


async def test_logging_executor_records_swap() -> None:
    """开发通道只记录 DDL，不连目标端（可观测地推进发布链路）。"""
    executor = LoggingSwapExecutor()
    sql = await executor.swap(target_table="t", shadow_table="t_shadow")
    assert sql.endswith("PROPERTIES('swap'='true');")
    assert executor.executed == [sql]


def test_doris_executor_implements_swap_protocol() -> None:
    """生产通道走 Doris FE MySQL 协议（复用 S3 探查依赖 aiomysql）。"""
    executor = DorisSwapExecutor(
        host="doris-fe", port=9030, user="etl_writer", password="pw", database="dw"
    )
    assert isinstance(executor, SwapExecutor)


def test_swap_executor_is_abstract() -> None:
    """`SwapExecutor` 是抽象通道（Schema 描述 + swap 执行）。"""
    with pytest.raises(TypeError):
        SwapExecutor()  # type: ignore[abstract]
