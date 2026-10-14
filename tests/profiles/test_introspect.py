"""表结构只读探查测试（T-07.2 / FR-12，S4 提示词第十节）。

覆盖：只读系统目录、无全表 COUNT、读表结构正确（列名 / 类型 / 可空 / 主键）。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.profiles import introspect, statistics
from etl_agent.secrets.factory import create_provider


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
