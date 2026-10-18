"""ClickHouse 元数据探查与近似统计（S11 T-21 / FR-12 / AC-02）。

覆盖：CH 纳入可内省类型；只读会话走 HTTP 通道；`system.columns` 取列与主键；
命名参数按位置映射（`{p1}` → `param_p1`）；`Nullable(...)` → nullable；
近似行数走 `system.parts`；目录语句无全表 COUNT。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from etl_agent.infrastructure.models import DataConnection
from etl_agent.profiles import introspect, statistics


def _connection(**overrides: object) -> DataConnection:
    base: dict[str, object] = {
        "name": "ch-analytics",
        "connector_type": "CLICKHOUSE",
        "host": "ch.local",
        "port": 8123,
        "database_name": "analytics",
    }
    base.update(overrides)
    return DataConnection(**base)  # type: ignore[arg-type]


class _FakeSession:
    """记录查询并按脚本返回行的只读会话替身。"""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self._responses = responses
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *params: object) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        return self._responses.pop(0)

    async def close(self) -> None:
        return None


def test_introspect_clickhouse_type_registered() -> None:
    """ClickHouse 纳入可内省类型（S11 T-21）。"""
    assert "CLICKHOUSE" in introspect.INTROSPECTABLE_TYPES


async def test_open_source_clickhouse_uses_http_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLICKHOUSE 连接必须走 HTTP 通道（不是 MySQL / PG 通道）。"""
    sentinel = object()
    opened: list[str] = []

    @asynccontextmanager
    async def _fake_clickhouse(connection: DataConnection, credentials: object) -> Any:
        opened.append(connection.connector_type)
        yield sentinel

    monkeypatch.setattr(introspect, "_open_clickhouse", _fake_clickhouse)

    async with introspect.open_source(_connection(), {}) as source:
        assert source is sentinel
    assert opened == ["CLICKHOUSE"]


async def test_clickhouse_session_maps_positional_params() -> None:
    """位置参数 → `{pN}` 命名参数；`default_format=JSON`；列名统一小写。"""

    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        async def post(self, url: str, *, params: dict[str, str]) -> Any:
            self.calls.append(params)
            return _Response({"data": [{"COLUMN_NAME": "order_id"}]})

    client = _Client()
    session = introspect._ClickHouseSession(client, "http://ch.local:8123/", "analytics")

    rows = await session.fetch("SELECT 1 WHERE a = {p1:String} AND b = {p2:String}", "x", 7)

    assert client.calls[0]["default_format"] == "JSON"
    assert client.calls[0]["database"] == "analytics"
    assert client.calls[0]["param_p1"] == "x"
    assert client.calls[0]["param_p2"] == "7"
    assert rows == [{"column_name": "order_id"}]


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


async def test_read_table_schema_clickhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    """`system.columns` 取列（`Nullable(...)` → 可空）+ 主键标记。"""
    session = _FakeSession(
        [
            [
                {"column_name": "order_id", "data_type": "UInt64", "nullable": 0},
                {"column_name": "amount", "data_type": "Nullable(Decimal(18, 2))", "nullable": 1},
            ],
            [{"column_name": "order_id"}],
        ]
    )

    @asynccontextmanager
    async def _fake_open(connection: DataConnection, credentials: object) -> Any:
        yield session

    monkeypatch.setattr(introspect, "open_source", _fake_open)

    schema = await introspect.read_table_schema(_connection(), {}, "analytics", "orders")

    columns_sql, columns_params = session.queries[0]
    assert "system.columns" in columns_sql
    assert "startsWith(type, 'Nullable(')" in columns_sql
    assert columns_params == ("analytics", "orders")

    pk_sql, pk_params = session.queries[1]
    assert "is_in_primary_key = 1" in pk_sql
    assert pk_params == ("analytics", "orders")

    assert [column.name for column in schema.columns] == ["order_id", "amount"]
    assert [column.nullable for column in schema.columns] == [False, True]
    assert [column.is_primary_key for column in schema.columns] == [True, False]


async def test_collect_statistics_clickhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    """近似行数走 `system.parts`；采样 `LIMIT {p1:UInt32}` 走命名参数。"""
    session = _FakeSession(
        [
            [{"approximate_rows": "1001"}],
            [
                {"order_id": 1, "amount": None},
                {"order_id": 2, "amount": 10},
            ],
        ]
    )

    @asynccontextmanager
    async def _fake_open(connection: DataConnection, credentials: object) -> Any:
        yield session

    monkeypatch.setattr(statistics, "open_source", _fake_open)

    result = await statistics.collect_statistics(_connection(), {}, "analytics", "orders", 5)

    approx_sql, approx_params = session.queries[0]
    assert "system.parts" in approx_sql
    assert "active" in approx_sql
    assert approx_params == ("analytics", "orders")

    sample_sql, sample_params = session.queries[1]
    assert sample_sql.endswith("LIMIT {p1:UInt32}")
    assert "`analytics`.`orders`" in sample_sql
    assert sample_params == (5,)

    assert result.approximate_row_count == 1001
    assert result.column_stats["amount"].null_rate == 0.5


def test_clickhouse_catalog_statements_are_read_only() -> None:
    """FR-12 红线：目录语句只读 system.*，且无全表 COUNT。"""
    statements = introspect.CLICKHOUSE_CATALOG_STATEMENTS
    assert statements
    for statement in statements:
        lowered = statement.lower()
        assert "count(" not in lowered
        assert "system." in lowered


def test_as_bool_clickhouse_flag() -> None:
    """ClickHouse 的 0/1 标记按布尔解析（与 PG bool / MySQL 'YES' 同口径）。"""
    assert introspect._as_bool(1) is True
    assert introspect._as_bool(0) is False
    assert introspect._as_bool("YES") is True
    assert introspect._as_bool("NO") is False
