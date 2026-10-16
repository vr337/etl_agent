"""表结构只读探查测试（T-07.2 / FR-12，S4 提示词第十节）。

覆盖：只读系统目录、无全表 COUNT、读表结构正确（列名 / 类型 / 可空 / 主键）。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.profiles import introspect, statistics
from etl_agent.secrets.factory import create_provider


def test_introspect_doris_type_registered() -> None:
    """Doris 纳入可内省类型（最小实现：FE 3.x 讲 MySQL 协议，走 MySQL 通道）。"""
    assert "DORIS" in introspect.INTROSPECTABLE_TYPES


async def test_open_source_doris_uses_mysql_channel(monkeypatch) -> None:
    """DORIS 连接的只读会话必须走 MySQL 通道（fix/S10-demo-script 最小实现）。"""
    from contextlib import asynccontextmanager

    from etl_agent.infrastructure.models import DataConnection

    sentinel = object()
    opened: list[str] = []

    @asynccontextmanager
    async def _fake_mysql(connection: DataConnection, credentials: object) -> object:
        opened.append(connection.connector_type)
        yield sentinel

    monkeypatch.setattr(introspect, "_open_mysql", _fake_mysql)
    connection = DataConnection(
        name="doris-spy",
        connector_type="DORIS",
        host="127.0.0.1",
        port=9030,
        database_name="doris_dw",
    )
    async with introspect.open_source(connection, {}) as source:
        assert source is sentinel
    assert opened == ["DORIS"]


class _FakeCursor:
    """返回大写列名行的假 DictCursor（模拟 MySQL 8.4 information_schema 行为）。"""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    async def execute(self, sql: str, params: object = None) -> None:
        return None

    async def fetchall(self) -> list[dict[str, object]]:
        return self._rows

    async def close(self) -> None:
        return None


async def test_mysql_session_lowercases_column_keys() -> None:
    """MySQL 通道统一小写键名：8.4 的 COLUMN_NAME 与 Doris 的 column_name 同构。"""
    from etl_agent.profiles.introspect import _MySQLSession

    session = _MySQLSession(raw=None, cursor=_FakeCursor([{"COLUMN_NAME": "order_id"}]))
    rows = await session.fetch("SELECT 1")
    assert rows == [{"column_name": "order_id"}]


def test_introspect_pg_catalog_only() -> None:
    """只读系统目录：语句全部命中 information_schema / pg_catalog，且无全表 COUNT（FR-12）。"""
    statements = introspect.PG_CATALOG_STATEMENTS + introspect.MYSQL_CATALOG_STATEMENTS
    assert statements
    for statement in statements:
        lowered = statement.lower()
        assert "count(" not in lowered
        assert "information_schema" in lowered or "pg_" in lowered


def test_introspect_pg_catalog_only_regex() -> None:
    """FR-12 红线静态核对（S4 提示词第十一节命令 9）：profiles 域无 COUNT(*) 全表扫描。"""
    for module in (introspect, statistics):
        source = Path(module.__file__ or "").read_text(encoding="utf-8").lower()
        assert "count(*)" not in source


async def test_introspect_schema(
    db_session: AsyncSession, seeded_connection: tuple
) -> None:
    """读表结构正确：列名 / 类型 / 可空 / 主键；全程只查系统目录。"""
    connection, settings = seeded_connection
    provider = create_provider(db_session, settings)
    credentials = await provider.read(connection.secret_ref)

    schema = await introspect.read_table_schema(
        connection, credentials, "public", "projects"
    )

    by_name = {column.name: column for column in schema.columns}
    assert {"id", "name", "created_at"} <= set(by_name)
    assert by_name["id"].is_primary_key is True
    assert by_name["name"].is_primary_key is False
    assert by_name["name"].nullable is False
    assert by_name["created_at"].data_type.startswith("timestamp")


async def test_statistics_uses_catalog_estimate(
    db_session: AsyncSession, seeded_connection: tuple, profile_source_table: str
) -> None:
    """近似统计：行数取目录估算、列统计来自采样（不做全表 COUNT，FR-12）。"""
    connection, settings = seeded_connection
    provider = create_provider(db_session, settings)
    credentials = await provider.read(connection.secret_ref)

    result = await statistics.collect_statistics(
        connection, credentials, "public", profile_source_table, 10
    )

    assert result.approximate_row_count >= 0
    assert 0 < len(result.rows) <= 10
    assert set(result.column_stats) == {
        "id",
        "customer_email",
        "amount",
        "created_at",
        "note",
    }
    for stat in result.column_stats.values():
        assert 0.0 <= stat.null_rate <= 1.0
        assert stat.distinct_estimate >= 0
