"""M-K 质量与发布 — 原子 Swap（Doris `REPLACE WITH TABLE ... PROPERTIES('swap')`）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 6.1 章 API-14 / 第 8 章「安全 · 数据面发布」
覆盖 FR：FR-43（Doris `CREATE LIKE` 预复制 + `REPLACE TABLE ... PROPERTIES('swap'='true')` 原子发布；同 Schema 强校验）
对应 AC：AC-07（质量报告 PASSED → 原子 Swap → 正式表发布成功）

S7 与 S8 的分界（禁止事项 15 / S7 禁止事项 16）：S7 只 `CREATE LIKE` 预复制 + 写入影子表；
**原子发布在本模块**，且必须通过 `publish.py` 的四道前置门禁后才允许执行。

原子性来自 Doris 的 swap 语义：`REPLACE WITH TABLE ... PROPERTIES('swap'='true')` 在元数据层
交换两张表的**名字**，不搬数据——因此对下游是「瞬时切换」，不存在「读到一半新一半旧」的窗口。
代价是 swap 之后影子表名字指向旧正式表数据，需由回滚链路决定保留或清理（FR-48）。

同 Schema 强校验（FR-43）：swap 会把影子表的结构**变成**正式表的结构，
Schema 不一致等于静默改表；本模块在 swap 前逐列比对列名与类型，不一致即拒绝执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from etl_agent.execution.errors import DependencyUnavailable, SwapSchemaMismatch

# Doris swap 发布语句（SPEC 3.2 M-K 原文形态）
SWAP_SQL_TEMPLATE = "ALTER TABLE {target} REPLACE WITH TABLE {shadow} PROPERTIES('swap'='true');"

# 列类型归一化：Doris 的 `DESC` 回传形态在不同版本间有大小写与长度差异
_TYPE_ALIASES: dict[str, str] = {
    "varchar": "string",
    "char": "string",
    "text": "string",
    "string": "string",
    "datetime": "timestamp",
    "date": "date",
    "decimal": "decimal",
    "bigint": "bigint",
    "int": "int",
    "tinyint": "tinyint",
    "double": "double",
    "float": "float",
    "boolean": "boolean",
    "json": "json",
    "array": "array",
    "map": "map",
}


@dataclass(frozen=True)
class TableColumn:
    """表列（列名 + 归一化后的类型）。"""

    name: str
    type: str


@dataclass(frozen=True)
class SchemaComparison:
    """同 Schema 校验结果（差异逐项列出，便于诊断直接引用）。"""

    aligned: bool
    missing_in_shadow: tuple[str, ...] = ()
    missing_in_target: tuple[str, ...] = ()
    type_mismatches: tuple[str, ...] = ()

    def summary(self) -> str:
        """差异摘要（只含列名与类型，不含数据，AC-02）。"""
        parts: list[str] = []
        if self.missing_in_shadow:
            parts.append(f"影子表缺少列 {', '.join(self.missing_in_shadow)}")
        if self.missing_in_target:
            parts.append(f"正式表缺少列 {', '.join(self.missing_in_target)}")
        if self.type_mismatches:
            parts.append(f"类型不一致 {', '.join(self.type_mismatches)}")
        return "；".join(parts) if parts else "Schema 一致"


@dataclass(frozen=True)
class SwapResult:
    """Swap 执行结果（治理事实，不含业务数据）。"""

    target_table: str
    shadow_table: str
    executed_sql: str
    swapped_at: datetime


def build_swap_sql(target_table: str, shadow_table: str) -> str:
    """构造 swap 语句（唯一构造点：避免 SQL 在别处被手拼出不一致的形态）。"""
    return SWAP_SQL_TEMPLATE.format(target=target_table, shadow=shadow_table)


def normalize_type(raw: str) -> str:
    """归一化列类型（去参数、小写、别名归一；未知类型原样保留）。"""
    text = raw.strip().lower()
    base = text.split("(", 1)[0].strip()
    return _TYPE_ALIASES.get(base, base)


def compare_schemas(
    target_columns: list[TableColumn], shadow_columns: list[TableColumn]
) -> SchemaComparison:
    """逐列比对（列名集合 + 类型），返回差异明细。"""
    target_map = {item.name.lower(): normalize_type(item.type) for item in target_columns}
    shadow_map = {item.name.lower(): normalize_type(item.type) for item in shadow_columns}
    missing_in_shadow = tuple(sorted(set(target_map) - set(shadow_map)))
    missing_in_target = tuple(sorted(set(shadow_map) - set(target_map)))
    mismatches = tuple(
        sorted(
            f"{name}（正式 {target_map[name]} / 影子 {shadow_map[name]}）"
            for name in set(target_map) & set(shadow_map)
            if target_map[name] != shadow_map[name]
        )
    )
    aligned = not (missing_in_shadow or missing_in_target or mismatches)
    return SchemaComparison(
        aligned=aligned,
        missing_in_shadow=missing_in_shadow,
        missing_in_target=missing_in_target,
        type_mismatches=mismatches,
    )


class SwapExecutor(ABC):
    """Swap 执行通道（Schema 描述 + swap 执行）。"""

    @abstractmethod
    async def describe_columns(self, table: str) -> list[TableColumn]:
        """读取表列定义（列名 + 类型）。"""

    @abstractmethod
    async def swap(self, *, target_table: str, shadow_table: str) -> str:
        """执行 swap 并返回实际执行的 SQL（幂等由调用方的发布状态机保证）。"""


async def check_schema_alignment(
    executor: SwapExecutor, *, target_table: str, shadow_table: str
) -> SchemaComparison:
    """读取两侧 Schema 并比对（FR-43 同 Schema 强校验）。"""
    target_columns = await executor.describe_columns(target_table)
    shadow_columns = await executor.describe_columns(shadow_table)
    return compare_schemas(target_columns, shadow_columns)


async def execute_atomic_swap(
    executor: SwapExecutor, *, target_table: str, shadow_table: str
) -> SwapResult:
    """执行原子 Swap（调用方已通过发布门禁；本函数只做「执行 + 留痕」）。

    Raises:
        SwapSchemaMismatch: 同 Schema 校验未通过（swap 前拦截，绝不改表结构）。
        DependencyUnavailable: 目标端不可达。
    """
    comparison = await check_schema_alignment(
        executor, target_table=target_table, shadow_table=shadow_table
    )
    if not comparison.aligned:
        raise SwapSchemaMismatch(
            f"影子表与正式表 Schema 不一致，拒绝 Swap（FR-43）：{comparison.summary()}"
        )
    sql = await executor.swap(target_table=target_table, shadow_table=shadow_table)
    return SwapResult(
        target_table=target_table,
        shadow_table=shadow_table,
        executed_sql=sql,
        swapped_at=datetime.now(UTC),
    )


class LoggingSwapExecutor(SwapExecutor):
    """开发 / 测试通道：只记录 DDL，不连目标端（无 Doris 时也可观测地推进发布链路）。

    默认两侧 Schema 一致（happy path 可跑）；`schemas` 可注入不一致的形态以验证门禁。
    """

    DEFAULT_COLUMNS: tuple[TableColumn, ...] = (
        TableColumn(name="id", type="bigint"),
        TableColumn(name="amount", type="decimal"),
        TableColumn(name="updated_at", type="timestamp"),
    )

    def __init__(self, schemas: dict[str, list[TableColumn]] | None = None) -> None:
        self._schemas = schemas or {}
        self.executed: list[str] = []
        self.swapped: list[tuple[str, str]] = []

    async def describe_columns(self, table: str) -> list[TableColumn]:
        if table in self._schemas:
            return list(self._schemas[table])
        return list(self.DEFAULT_COLUMNS)

    async def swap(self, *, target_table: str, shadow_table: str) -> str:
        sql = build_swap_sql(target_table, shadow_table)
        self.executed.append(sql)
        self.swapped.append((target_table, shadow_table))
        return sql


class DorisSwapExecutor(SwapExecutor):
    """Doris FE MySQL 协议通道（生产目标态；复用 S3 探查依赖 aiomysql）。"""

    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        # 口令只用于建立一次性连接，不落日志（AC-02）
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database

    async def describe_columns(self, table: str) -> list[TableColumn]:
        import aiomysql

        connection = await self._connect(aiomysql)
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(f"DESC {table}")
                rows = await cursor.fetchall()
        finally:
            connection.close()
        # DESC 回传 (Field, Type, Null, Key, Default, Extra)
        return [TableColumn(name=str(row[0]), type=str(row[1])) for row in rows]

    async def swap(self, *, target_table: str, shadow_table: str) -> str:
        import aiomysql

        sql = build_swap_sql(target_table, shadow_table)
        connection = await self._connect(aiomysql)
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(sql)
        finally:
            connection.close()
        return sql

    async def _connect(self, aiomysql: Any) -> Any:
        """建立一次性连接（不可达统一映射为依赖不可用，不含口令）。"""
        try:
            return await aiomysql.connect(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                db=self._database,
            )
        except Exception as exc:
            raise DependencyUnavailable(
                f"Doris 不可达：{type(exc).__name__}（原子发布失败）"
            ) from exc


__all__ = [
    "SWAP_SQL_TEMPLATE",
    "DorisSwapExecutor",
    "LoggingSwapExecutor",
    "SchemaComparison",
    "SwapExecutor",
    "SwapResult",
    "TableColumn",
    "build_swap_sql",
    "check_schema_alignment",
    "compare_schemas",
    "execute_atomic_swap",
    "normalize_type",
]
