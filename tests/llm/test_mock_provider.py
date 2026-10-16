"""MockProvider HOCON 渲染用例（T-ENGINE-02 缺陷 1：driver/URL 按 connector_type）。

`compliant_hocon` 在 mock 时代只被 gate 测试间接使用；真引擎首跑暴露
「硬编码 PG driver 连 MySQL 协议端口」后，渲染必须按连接器类型分流。
"""
from __future__ import annotations

import pytest

from etl_agent.llm.mock_provider import compliant_hocon


def _profile(connector_type: str, table: str = "orders") -> dict[str, object]:
    return {
        "connector_type": connector_type,
        "connection_id": "11111111-1111-1111-1111-111111111111",
        "schema_name": "source_db",
        "table_name": table,
        "endpoint_role": "SOURCE",
        "columns": [{"name": "id", "type": "bigint", "nullable": False}],
    }


def test_hocon_mysql_source_uses_mysql_driver() -> None:
    hocon = compliant_hocon(_profile("MYSQL"), _profile("MYSQL", "order_wide"))
    assert 'jdbc:mysql://${SRC_HOST}:${SRC_PORT}/${SRC_DB}' in hocon
    assert 'com.mysql.cj.jdbc.Driver' in hocon
    assert 'jdbc:postgresql://' not in hocon


def test_hocon_doris_sink_uses_mysql_driver() -> None:
    """Doris FE 讲 MySQL 协议 → 与 MYSQL 同 driver（T-ENGINE-02）。"""
    hocon = compliant_hocon(_profile("MYSQL"), _profile("DORIS", "order_wide"))
    assert 'jdbc:mysql://${SINK_HOST}:${SINK_PORT}/${SINK_DB}' in hocon
    assert 'driver = "com.mysql.cj.jdbc.Driver"' in hocon


def test_hocon_postgresql_keeps_original_shape() -> None:
    """POSTGRESQL 保持原渲染形态（向后兼容，不回归）。"""
    hocon = compliant_hocon(_profile("POSTGRESQL"), _profile("POSTGRESQL"))
    assert 'jdbc:postgresql://${SRC_HOST}:${SRC_PORT}/${SRC_DB}' in hocon
    assert 'org.postgresql.Driver' in hocon


def test_hocon_unknown_connector_falls_back_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """未覆盖类型：回落 POSTGRESQL 且 warning（不静默——首版教训）。"""
    with caplog.at_level("WARNING"):
        hocon = compliant_hocon(_profile("ORACLE"), _profile("ORACLE"))
    assert 'org.postgresql.Driver' in hocon
    assert any("ORACLE" in record.message for record in caplog.records)


def test_hocon_placeholders_and_queries_preserved() -> None:
    """占位符契约（FR-68）与 query 表名插值不回归。"""
    hocon = compliant_hocon(_profile("MYSQL", "orders"), _profile("DORIS", "order_wide"))
    for placeholder in ("${SRC_USER}", "${SRC_PASSWORD}", "${SINK_USER}", "${SINK_PASSWORD}"):
        assert placeholder in hocon
    assert 'query = "select * from orders"' in hocon
    assert 'query = "insert into order_wide"' in hocon
    assert "job.mode" in hocon
