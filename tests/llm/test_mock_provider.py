"""MockProvider HOCON 渲染用例。

T-ENGINE-02 缺陷 1：driver/URL 按 connector_type（真引擎首跑暴露
「硬编码 PG driver 连 MySQL 协议端口」）。
T-ENGINE-03：sink 由 `query` 改 `table` 形态（`query` 模式 SeaTunnel 原样下发、
不补 VALUES 子句 → Doris 解析必败）。
T-SHADOW-01：双路分流（valid → 影子表 / error → 错误表）+ 三条质量规则。
"""
from __future__ import annotations

import pytest

from etl_agent.llm.mock_provider import compliant_hocon, compliant_plan

# 演示画像的真实列（与 e2e seed 一致）——质量规则与投影都按列名渲染，
# 用假列名会让渲染出的 SQL 在语义上不自洽（测试也读不出问题）
_DEMO_COLUMNS = [
    {"name": "order_id", "type": "bigint", "nullable": False},
    {"name": "customer_id", "type": "bigint", "nullable": False},
    {"name": "amount", "type": "decimal(18, 2)", "nullable": True},
    {"name": "status", "type": "varchar(16)", "nullable": False},
    {"name": "address", "type": "varchar(255)", "nullable": True},
    {"name": "updated_at", "type": "datetime", "nullable": False},
]


def _profile(
    connector_type: str,
    table: str = "orders",
    schema: str = "source_db",
    columns: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "connector_type": connector_type,
        "connection_id": "11111111-1111-1111-1111-111111111111",
        "schema_name": schema,
        "table_name": table,
        "endpoint_role": "SOURCE",
        "columns": columns if columns is not None else _DEMO_COLUMNS,
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
    assert 'table = "order_wide_shadow"' in hocon
    assert 'database = "doris_dw"' in hocon
    assert hocon.count("generate_sink_sql = true") == 2
    assert hocon.count("enable_upsert = false") == 2
    # sink 段不得再有生效的 query 行（旧形态已弃用）
    sink_section = hocon.split("sink {", 1)[1]
    assert "\n    query = " not in sink_section


def test_hocon_sink_table_and_database_follow_profile() -> None:
    """T-ENGINE-03/T-SHADOW-01：table / database 取自 sink profile，不硬编码。"""
    hocon = compliant_hocon(
        _profile("MYSQL", "orders"),
        _profile("DORIS", "custom_wide", schema="custom_dw"),
    )
    assert 'table = "custom_wide_shadow"' in hocon
    assert 'table = "custom_wide_error"' in hocon
    assert 'database = "custom_dw"' in hocon
    sink_section = hocon.split("sink {", 1)[1]
    assert "doris_dw" not in sink_section


def test_hocon_dual_path_routing() -> None:
    """T-SHADOW-01：双路分流——source 打标签，两个 Sql 切成 valid/error，两个 Jdbc 消费。

    SeaTunnel 2.3.13 实测：同类型 plugin 可重复声明（归一化为列表），
    `plugin_output`/`plugin_input` 路由生效。
    """
    hocon = compliant_hocon(
        _profile("MYSQL", "orders"),
        _profile("DORIS", "order_wide", schema="doris_dw"),
    )
    assert 'plugin_output = "src"' in hocon
    # 两条 transform：valid / error 各一路
    assert hocon.count('plugin_output = "valid"') == 1
    assert hocon.count('plugin_output = "error"') == 1
    assert hocon.count("  Sql {") == 2
    # 两个 sink：plugin_input 分别消费两路
    assert 'plugin_input = "valid"' in hocon
    assert 'plugin_input = "error"' in hocon
    assert hocon.count("  Jdbc {") == 3  # 1 source + 2 sink
    # 有效路 = 全部规则通过与；错误路 = 任一规则不通过（**直接否定态**）
    assert "where (order_id IS NOT NULL) and (amount IS NOT NULL) and (updated_at <= CURRENT_TIMESTAMP)" in hocon
    assert "where (order_id IS NULL) or (amount IS NULL) or (updated_at > CURRENT_TIMESTAMP)" in hocon
    # 禁用 `not (...)` 包裹：SeaTunnel Sql transform 不支持（run22 实证 COMMON-05）
    assert "not (" not in hocon.split("transform {", 1)[1].split("sink {", 1)[0]


def test_hocon_error_branch_case_priority_and_codes() -> None:
    """T-SHADOW-01：error_code 用 CASE 按规则声明顺序取首命中（一行恰好一条错误记录）。

    单分支 + CASE（而非每规则各写一次）是为了让 `rejected_rows` 不被重复计数污染，
    且 code 是单值、可直接按 `rules[].code` 聚合（PRD 第 10 章）。
    """
    hocon = compliant_hocon(
        _profile("MYSQL", "orders"),
        _profile("DORIS", "order_wide", schema="doris_dw"),
    )
    # 顺序即优先级：PK > AMOUNT > WATERMARK
    pk_at = hocon.index("'QUALITY_NOT_NULL_PK'")
    amount_at = hocon.index("'QUALITY_NOT_NULL_AMOUNT'")
    watermark_at = hocon.index("'QUALITY_FUTURE_WATERMARK'")
    assert pk_at < amount_at < watermark_at
    assert "else 'QUALITY_UNKNOWN' end as error_code" in hocon
    assert "as error_reason" in hocon
    # 错误表那一路必须落在 <目标表>_error
    assert 'table = "order_wide_error"' in hocon


def test_plan_contract_uses_unified_names_and_codes() -> None:
    """T-SHADOW-01/T-SHADOW-02：契约命名与 execution.shadow 派生名一致 + 规则带 code。"""
    plan = compliant_plan("需求", _profile("MYSQL", "orders"), _profile("DORIS", "order_wide"))
    contract = plan["quality_contract"]
    assert contract["valid_sink"] == "order_wide_shadow"
    assert contract["error_sink"] == "order_wide_error"
    predicates = contract["predicates"]
    assert [item["code"] for item in predicates] == [
        "QUALITY_NOT_NULL_PK",
        "QUALITY_NOT_NULL_AMOUNT",
        "QUALITY_FUTURE_WATERMARK",
    ]
    # routing 的 sink 必须落在已声明的 valid/error 之内（GATE-005）
    declared = {contract["valid_sink"], contract["error_sink"]}
    assert all(item["sink"] in declared for item in contract["routing"])


def test_plan_contract_declares_reject_threshold() -> None:
    """T-QUALITY-01：契约**必须**显式声明 `max_reject_rate`，否则质量判定 fail-open。

    回归口径：`classify_quality` 只在 `max_rejected_rows` / `max_reject_rate`
    至少一个成立时才可能判 FAILED/WARNING；两者都缺 → 永远 PASSED，
    发布门禁第 2 道形同虚设。此前 `mock_provider` 与 `tests/conftest.py` 的
    `S6_TEST_PLAN` 都没声明，`test_consume_outbox_happy_path` 那句
    「拒绝率 10% 未超阈值」的注释是靠这个缺口活的（见 fix/final-gate）。
    """
    plan = compliant_plan("需求", _profile("MYSQL", "orders"), _profile("DORIS", "order_wide"))
    contract = plan["quality_contract"]
    rate = contract.get("max_reject_rate")
    assert isinstance(rate, (int, float)) and not isinstance(rate, bool)
    assert 0 < float(rate) < 1, f"阈值应在 (0,1) 开区间，实际 {rate!r}"

    # 演示 seed 的拒绝率（3/1003 ≈ 0.30%）必须落在阈值内，取证口径才不变
    assert float(rate) > 3 / 1003 * 2, "阈值应显著高于演示 seed 的拒绝率"
