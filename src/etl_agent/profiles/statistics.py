"""M-D 元数据探查与数据分级 — 近似统计（采样，不做全表扫描）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-D / 第 8 章「性能」行
覆盖 FR：FR-12（近似统计：行数取目录近似值，列基数 / 空值率取采样）
对应 AC：AC-02（采样行只在内存中短暂持有，脱敏后才落库）

**近似原理**（S4 提示词 9.1-4）：
- 行数：PG 取 `pg_class.reltuples`、MySQL 取 `information_schema.tables.table_rows`，
  均为优化器/统计信息维护的近似值，代价 O(1)，**不做全表 COUNT**（FR-12 红线）；
- 列基数与空值率：`SELECT * FROM <表> LIMIT N`（N = sample_limit，1–100）。
  未选 `TABLESAMPLE`：PG 的 SYSTEM 采样按数据页抽取，小于一页的表会返回空集，
  而 `LIMIT` 在两种探针上语义一致，两者同属近似，故取可移植者。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from etl_agent.infrastructure.models import DataConnection
from etl_agent.profiles.introspect import open_source, qualified_table

# 目录级近似行数（FR-12：O(1) 代价，绝不扫描业务表）
# GREATEST/COALESCE 收敛哨兵值：PG 的 reltuples < 0 表示该表从未被 ANALYZE、
# MySQL 的 table_rows 可为 NULL —— 两者都属「目录无估算」，不应把 -1 透出 API。
PG_APPROX_ROWS_SQL = """
SELECT GREATEST(c.reltuples, 0)::bigint AS approximate_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND c.relname = $2
"""

MYSQL_APPROX_ROWS_SQL = """
SELECT COALESCE(table_rows, 0) AS approximate_rows
FROM information_schema.tables
WHERE table_schema = %s AND table_name = %s
"""

# ClickHouse：`system.parts` 的 active 分片行数之和（系统表，O(分片数)，
# 不触碰业务表 —— 与 FR-12「绝不扫描业务表」的红线一致）。
CH_APPROX_ROWS_SQL = """
SELECT COALESCE(sum(rows), 0) AS approximate_rows
FROM system.parts
WHERE database = {p1:String} AND table = {p2:String} AND active
"""

# 连接器类型 → 近似行数语句（缺省按 MySQL 通道）
_APPROX_ROWS_SQL_BY_TYPE: dict[str, str] = {
    "POSTGRESQL": PG_APPROX_ROWS_SQL,
    "CLICKHOUSE": CH_APPROX_ROWS_SQL,
}


@dataclass(frozen=True)
class ColumnStat:
    """列级近似统计（基于采样，非全表精确值）。"""

    null_rate: float
    distinct_estimate: int


@dataclass(frozen=True)
class ProfileStatistics:
    """探查统计结果：近似行数 + 采样原始行（内存中短暂持有）+ 列级统计。"""

    approximate_row_count: int
    rows: list[dict[str, Any]]
    column_stats: dict[str, ColumnStat]


def sample_sql(schema_name: str, table_name: str, *, dialect: str, limit_placeholder: str) -> str:
    """构造采样查询（标识符经白名单校验，行数走参数占位符）。"""
    table = qualified_table(schema_name, table_name, dialect=dialect)
    return f"SELECT * FROM {table} LIMIT {limit_placeholder}"


async def collect_statistics(
    connection: DataConnection,
    credentials: Mapping[str, str],
    schema_name: str,
    table_name: str,
    sample_limit: int,
) -> ProfileStatistics:
    """采集近似统计与脱敏前样本（FR-12）。

    Args:
        connection: 连接登记信息。
        credentials: SecretProvider 解密的凭据（内存中短暂持有）。
        schema_name / table_name: 受校验的源表标识。
        sample_limit: 采样行数上限（1–100，由 service 层校验）。

    Returns:
        ProfileStatistics: 近似行数 + 原始采样行 + 列级统计。
        原始行仅用于同一请求内的脱敏（`masking.mask_samples`），禁止落库/日志。

    Raises:
        ConnectorUnsupported: 连接器类型无探查通道。
        ValidationError:      标识符非法。
    """
    connector = connection.connector_type
    is_postgres = connector == "POSTGRESQL"
    is_clickhouse = connector == "CLICKHOUSE"
    # ClickHouse 的标识符包裹与 MySQL 同形（反引号），故复用 mysql 方言
    dialect = "postgres" if is_postgres else "mysql"
    # 采样查询只有 1 个绑定参数（行数上限）：
    # PG 用 $1、MySQL 用 %s、ClickHouse HTTP 用命名参数 {p1:UInt32}
    if is_postgres:
        limit_placeholder = "$1"
    elif is_clickhouse:
        limit_placeholder = "{p1:UInt32}"
    else:
        limit_placeholder = "%s"

    async with open_source(connection, credentials) as source:
        row_count_rows = await source.fetch(
            _APPROX_ROWS_SQL_BY_TYPE.get(connector, MYSQL_APPROX_ROWS_SQL),
            schema_name,
            table_name,
        )
        rows = await source.fetch(
            sample_sql(
                schema_name, table_name, dialect=dialect, limit_placeholder=limit_placeholder
            ),
            sample_limit,
        )

    approximate_row_count = 0
    if row_count_rows:
        approximate_row_count = int(row_count_rows[0].get("approximate_rows") or 0)
    return ProfileStatistics(
        approximate_row_count=approximate_row_count,
        rows=rows,
        column_stats=compute_column_stats(rows),
    )


def compute_column_stats(rows: list[dict[str, Any]]) -> dict[str, ColumnStat]:
    """由采样行推导列级空值率与基数（近似值）。

    Returns:
        列名 → ColumnStat；空样本返回空字典（由调用方按「无统计」处理）。
    """
    if not rows:
        return {}
    sample_size = len(rows)
    column_names: list[str] = []
    for row in rows:
        for name in row:
            if name not in column_names:
                column_names.append(name)
    stats: dict[str, ColumnStat] = {}
    for name in column_names:
        values = [row.get(name) for row in rows]
        null_count = sum(1 for value in values if value is None)
        # 采样内去重：NULL 不计入基数（与 SQL 的 COUNT(DISTINCT) 语义一致）
        distinct = len({_hashable(value) for value in values if value is not None})
        stats[name] = ColumnStat(
            null_rate=round(null_count / sample_size, 4),
            distinct_estimate=distinct,
        )
    return stats


def _hashable(value: Any) -> Any:
    """把不可哈希的列值（list/dict）转为可比较的稳定表示，供基数估算。"""
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), _hashable(item)) for key, item in value.items()))
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return value
