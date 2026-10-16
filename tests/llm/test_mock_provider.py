"""MockProvider HOCON 渲染用例。

T-ENGINE-02 缺陷 1：driver/URL 按 connector_type（真引擎首跑暴露
「硬编码 PG driver 连 MySQL 协议端口」）。
T-ENGINE-03：sink 由 `query` 改 `table` 形态（`query` 模式 SeaTunnel 原样下发、
不补 VALUES 子句 → Doris 解析必败）。
"""
from __future__ import annotations

import pytest

from etl_agent.llm.mock_provider import compliant_hocon


def _profile(
    connector_type: str, table: str = "orders", schema: str = "source_db"
) -> dict[str, object]:
    return {
        "connector_type": connector_type,
        "connection_id": "11111111-1111-1111-1111-111111111111",
        "schema_name": schema,
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


def test_hocon_placeholders_and_source_query_preserved() -> None:
    """占位符契约（FR-68）与 source query 表名插值不回归。"""
    hocon = compliant_hocon(_profile("MYSQL", "orders"), _profile("DORIS", "order_wide"))
    for placeholder in ("${SRC_USER}", "${SRC_PASSWORD}", "${SINK_USER}", "${SINK_PASSWORD}"):
        assert placeholder in hocon
    assert 'query = "select * from orders"' in hocon
    assert "job.mode" in hocon


def test_hocon_sink_uses_table_form_not_query() -> None:
    """T-ENGINE-03：sink 改 `table` 形态，四个选项缺一不可。

    SeaTunnel `query` 模式把 SQL 原样作为 PreparedStatement 下发、不补 VALUES，
    `insert into <表>` 被 Doris 解析器拒绝；`table` 形态由连接器按目标表元数据
    生成完整 INSERT。真引擎逐级实测（缺任一选项即失败）：
    缺 generate_sink_sql → 仍要求 query；缺 database → 校验失败；
    enable_upsert 默认 true → 追加 `ON DUPLICATE KEY UPDATE`，Doris 不支持该语法。
    """
    hocon = compliant_hocon(
        _profile("MYSQL", "orders"),
        _profile("DORIS", "order_wide", schema="doris_dw"),
    )
    assert 'table = "order_wide"' in hocon
    assert 'database = "doris_dw"' in hocon
    assert "generate_sink_sql = true" in hocon
    assert "enable_upsert = false" in hocon
    # 旧 query 形态仅保留为注释（不得作为生效配置出现在 sink 段）
    assert '# query = "insert into order_wide"' in hocon
    sink_section = hocon.split("sink {", 1)[1]
    assert "\n    query = " not in sink_section, "sink 段不得再有生效的 query 行"


def test_hocon_sink_table_and_database_follow_profile() -> None:
    """T-ENGINE-03：table / database 取自 sink profile，不硬编码。"""
    hocon = compliant_hocon(
        _profile("MYSQL", "orders"),
        _profile("DORIS", "custom_wide", schema="custom_dw"),
    )
    assert 'table = "custom_wide"' in hocon
    assert 'database = "custom_dw"' in hocon
    assert "doris_dw" not in hocon.split("sink {", 1)[1]
