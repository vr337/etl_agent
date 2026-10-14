"""M-D 元数据探查与数据分级 — 表结构只读探查（禁全表 COUNT）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-D / 第 8 章「性能」行
覆盖 FR：FR-12（表级只读 Profile：Schema / 字段类型 / 主键；不执行全表 COUNT）
对应 AC：AC-02（只读系统目录，不落样本明文）

**FR-12 红线（S4 禁止事项 14）**：只读 `information_schema` / `pg_catalog` 系统目录，
绝不对业务表执行全表计数（聚合 COUNT）—— 在源库大表上等价于全表扫描，把「探查」
变成对生产库的压测；行数一律取目录统计的近似值（见 `statistics.py`）。

同时承载源库连接工厂：`open_source()` 供 `statistics.py` 复用，避免两处各写一份
连接与凭据装配逻辑（凭据只在内存中短暂持有，S3 禁止事项 16）。
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from etl_agent.auth.errors import ValidationError
from etl_agent.connections.errors import ConnectorUnsupported
from etl_agent.infrastructure.models import DataConnection

# 探针实装的连接器类型（与 connections.probes 的实装范围一致）：
# 其余类型（Oracle/SQLServer/Doris/Hive/HDFS/LOCAL_FILE）无元数据探查通道，
# 统一返回 AMEND-002 的 BIZ_422_CONNECTOR_UNSUPPORTED，不做静默降级。
INTROSPECTABLE_TYPES = frozenset({"POSTGRESQL", "MYSQL"})

# 标识符白名单：库名 / 表名来自请求体，必须以白名单校验后双引号包裹，
# 杜绝通过 schema_name 注入 SQL（参数占位符无法覆盖标识符位置）。
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")

# ============================================================
# 只读目录查询（FR-12：全部命中系统目录，无任何业务表扫描）
# ============================================================

PG_COLUMNS_SQL = """
SELECT a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND c.relname = $2 AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

PG_PRIMARY_KEY_SQL = """
SELECT a.attname AS column_name
FROM pg_index i
JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND c.relname = $2 AND i.indisprimary
ORDER BY a.attnum
"""

MYSQL_COLUMNS_SQL = """
SELECT column_name, column_type AS data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = %s AND table_name = %s
ORDER BY ordinal_position
"""

MYSQL_PRIMARY_KEY_SQL = """
SELECT column_name
FROM information_schema.key_column_usage
WHERE table_schema = %s AND table_name = %s AND constraint_name = 'PRIMARY'
ORDER BY ordinal_position
"""

# 供 FR-12 红线做静态核对（S4 提示词第十一节命令 9）：以下语句集中声明只读契约，
# 任何新增查询都必须落在这里，避免「顺手写个 COUNT」绕过评审。
PG_CATALOG_STATEMENTS: tuple[str, ...] = (PG_COLUMNS_SQL, PG_PRIMARY_KEY_SQL)
MYSQL_CATALOG_STATEMENTS: tuple[str, ...] = (MYSQL_COLUMNS_SQL, MYSQL_PRIMARY_KEY_SQL)


@dataclass(frozen=True)
class ColumnInfo:
    """源表字段（FR-12：名称 / 类型 / 可空 / 是否主键）。"""

    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool


@dataclass(frozen=True)
class TableSchema:
    """源表结构（不含任何行数据）。"""

    schema_name: str
    table_name: str
    columns: list[ColumnInfo]


class SourceSession(Protocol):
    """源库只读会话：仅暴露参数化查询，杜绝调用方拼接 SQL。"""

    async def fetch(self, sql: str, *params: object) -> list[dict[str, Any]]:
        """执行参数化查询并返回行字典列表。"""
        ...

    async def close(self) -> None:
        """释放连接。"""
        ...


class TableNotFound(Exception):
    """源表不存在或当前账号无元数据读取权限（由 service 映射为 BIZ_422_PROBE_FAILED）。"""


def quote_identifier(identifier: str, *, dialect: str) -> str:
    """标识符白名单校验 + 方言包裹（SQL 注入防护）。

    Args:
        identifier: 库名 / 表名（来自请求体，不可信）。
        dialect: postgres | mysql，决定包裹符。

    Returns:
        已包裹的标识符（postgres 用双引号，mysql 用反引号）。

    Raises:
        ValidationError: 标识符含白名单外字符（BIZ_400_VALIDATION）。
    """
    if not _IDENTIFIER_PATTERN.match(identifier):
        raise ValidationError(
            f"非法标识符: {identifier!r}（仅允许字母/数字/下划线/$，且不以数字开头）"
        )
    if dialect == "mysql":
        return f"`{identifier}`"
    return f'"{identifier}"'


def qualified_table(schema_name: str, table_name: str, *, dialect: str) -> str:
    """拼装 `schema.table` 形式的受校验表引用（供采样式查询复用）。"""
    return (
        f"{quote_identifier(schema_name, dialect=dialect)}."
        f"{quote_identifier(table_name, dialect=dialect)}"
    )


@asynccontextmanager
async def open_source(
    connection: DataConnection, credentials: Mapping[str, str]
) -> AsyncIterator[SourceSession]:
    """打开源库只读会话（PG / MySQL）。

    Args:
        connection: 连接登记信息（定位参数）。
        credentials: SecretProvider 解密的凭据，**只在内存中短暂持有**（S3 禁止事项 16）。

    Yields:
        SourceSession: 只读会话，退出上下文即关闭。

    Raises:
        ConnectorUnsupported: 该连接器类型无元数据探查通道（BIZ_422_CONNECTOR_UNSUPPORTED）。
    """
    if connection.connector_type == "POSTGRESQL":
        async with _open_postgres(connection, credentials) as session:
            yield session
        return
    if connection.connector_type == "MYSQL":
        async with _open_mysql(connection, credentials) as session:
            yield session
        return
    raise ConnectorUnsupported(
        f"connector type not yet supported for metadata introspection: {connection.connector_type}"
    )


@asynccontextmanager
async def _open_postgres(
    connection: DataConnection, credentials: Mapping[str, str]
) -> AsyncIterator[SourceSession]:
    """PostgreSQL 只读会话（asyncpg）。"""
    import asyncpg

    raw = await asyncpg.connect(
        host=connection.host or "",
        port=connection.port or 5432,
        user=credentials.get("username", ""),
        password=credentials.get("password", ""),
        database=connection.database_name,
        timeout=10,
    )
    try:
        yield _PostgresSession(raw)
    finally:
        await raw.close()


@asynccontextmanager
async def _open_mysql(
    connection: DataConnection, credentials: Mapping[str, str]
) -> AsyncIterator[SourceSession]:
    """MySQL 只读会话（aiomysql，DictCursor）。"""
    import aiomysql

    raw = await aiomysql.connect(
        host=connection.host or "",
        port=connection.port or 3306,
        user=credentials.get("username", ""),
        password=credentials.get("password", ""),
        db=connection.database_name,
        cursorclass=aiomysql.DictCursor,
        autocommit=True,
        connect_timeout=10,
    )
    try:
        cursor = await raw.cursor()
        try:
            yield _MySQLSession(raw, cursor)
        finally:
            await cursor.close()
    finally:
        raw.close()


class _PostgresSession:
    """asyncpg 连接的只读封装。"""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    async def fetch(self, sql: str, *params: object) -> list[dict[str, Any]]:
        records = await self._raw.fetch(sql, *params)
        return [dict(record) for record in records]

    async def close(self) -> None:
        await self._raw.close()


class _MySQLSession:
    """aiomysql 连接的只读封装。"""

    def __init__(self, raw: Any, cursor: Any) -> None:
        self._raw = raw
        self._cursor = cursor

    async def fetch(self, sql: str, *params: object) -> list[dict[str, Any]]:
        await self._cursor.execute(sql, params or None)
        rows = await self._cursor.fetchall()
        return [dict(row) for row in rows]

    async def close(self) -> None:
        await self._cursor.close()
        self._raw.close()


async def read_table_schema(
    connection: DataConnection,
    credentials: Mapping[str, str],
    schema_name: str,
    table_name: str,
) -> TableSchema:
    """读取源表结构（FR-12：Schema / 类型 / 主键，不含任何行数据）。

    Raises:
        ValidationError:       schema_name / table_name 非法标识符。
        ConnectorUnsupported:  连接器类型无探查通道。
        TableNotFound:         源库中不存在该表（库/表名错误，或当前账号无元数据权限）。
    """
    # 先校验标识符：非法输入不应建立任何源库连接（避免把校验失败变成源库压力）
    quote_identifier(schema_name, dialect="postgres")
    quote_identifier(table_name, dialect="postgres")

    async with open_source(connection, credentials) as source:
        is_postgres = connection.connector_type == "POSTGRESQL"
        columns_sql = PG_COLUMNS_SQL if is_postgres else MYSQL_COLUMNS_SQL
        primary_key_sql = PG_PRIMARY_KEY_SQL if is_postgres else MYSQL_PRIMARY_KEY_SQL
        column_rows = await source.fetch(columns_sql, schema_name, table_name)
        if not column_rows:
            raise TableNotFound(
                f"源表不存在或无元数据读取权限: {schema_name}.{table_name}"
            )
        primary_key_rows = await source.fetch(primary_key_sql, schema_name, table_name)

    primary_keys = {str(row["column_name"]) for row in primary_key_rows}
    columns = [
        ColumnInfo(
            name=str(row["column_name"]),
            data_type=str(row["data_type"]),
            # PG 返回布尔；MySQL 返回 'YES'/'NO'
            nullable=_as_bool(row["nullable"]),
            is_primary_key=str(row["column_name"]) in primary_keys,
        )
        for row in column_rows
    ]
    return TableSchema(schema_name=schema_name, table_name=table_name, columns=columns)


def _as_bool(value: object) -> bool:
    """可空标记归一化（PG bool / MySQL 'YES'|'NO'）。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() == "YES"
