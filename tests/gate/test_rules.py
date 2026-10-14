"""14 项门禁规则测试（T-10；S5 提示词第十二节 tests/gate）。

覆盖：14 项规则全注册、合规候选全通过，以及每项规则的失败分支
（远超 DoD-7 要求的「至少 6 项规则有对应失败用例」）。
"""
from __future__ import annotations

from etl_agent.gate.contracts import RuleResult, all_rules, rule_ids
from etl_agent.gate.engine_check import LocalStubEngineChecker
from etl_agent.gate.service import run_gate

CONNECTION_ID = "11111111-1111-1111-1111-111111111111"
SINK_CONNECTION_ID = "22222222-2222-2222-2222-222222222222"

COMPLIANT_HOCON = """env {
  job.mode = "BATCH"
}
source {
  Jdbc {
    url = "jdbc:postgresql://${SRC_HOST}:5432/etl_agent"
    user = "${SRC_USER}"
    password = "${SRC_PASSWORD}"
    query = "select id, amount from orders"
  }
}
sink {
  Jdbc {
    url = "jdbc:postgresql://${SINK_HOST}:5432/etl_agent"
    user = "${SINK_USER}"
    password = "${SINK_PASSWORD}"
    query = "insert into orders_wide"
  }
}
"""


def base_plan() -> dict:
    """合规 EtlPlan 基线（各用例在此基础上注入单点缺陷）。"""
    return {
        "pipeline_name": "orders_wide",
        "backend": "direct",
        "source": {
            "connector_type": "POSTGRESQL",
            "connection_id": CONNECTION_ID,
            "table": "orders",
        },
        "sink": {
            "connector_type": "POSTGRESQL",
            "connection_id": SINK_CONNECTION_ID,
            "table": "orders_wide",
            "target_schema": {
                "columns": [
                    {"name": "id", "type": "bigint"},
                    {"name": "amount", "type": "numeric"},
                ]
            },
        },
        "transforms": [{"type": "sql", "sql": "select id, amount from orders"}],
        "quality_contract": {
            "valid_sink": "orders_wide_valid",
            "error_sink": "orders_wide_error",
            "routing": [
                {"rule": "not_null_id", "sink": "orders_wide_valid"},
                {"rule": "default", "sink": "orders_wide_error"},
            ],
            "traceability": {
                "source_row_id_column": "id",
                "error_code_column": "error_code",
                "reason_column": "error_reason",
            },
            "predicates": [{"name": "not_null_id", "expression": "id IS NOT NULL"}],
        },
    }


def base_context() -> dict:
    """门禁上下文基线（画像 + 当前项目白名单）。"""
    return {
        "project_id": "33333333-3333-3333-3333-333333333333",
        "connection_ids": [CONNECTION_ID, SINK_CONNECTION_ID],
        "file_asset_ids": [],
        "source_profile": {
            "endpoint_role": "SOURCE",
            "table_name": "orders",
            "columns": [
                {"name": "id", "type": "bigint"},
                {"name": "amount", "type": "numeric"},
                {"name": "updated_at", "type": "timestamptz"},
            ],
        },
        "sink_profile": {
            "endpoint_role": "SINK",
            "table_name": "orders_wide",
            "columns": [
                {"name": "id", "type": "bigint"},
                {"name": "amount", "type": "numeric"},
            ],
        },
    }


def check_one(rule_id: str, plan: dict, hocon: str | None = None, context: dict | None = None) -> RuleResult:
    """只跑单项规则（按 rule_id 定位，避免用例之间互相干扰）。"""
    rule = next(item for item in all_rules() if item.rule_id == rule_id)
    return rule.check(plan, COMPLIANT_HOCON if hocon is None else hocon, context or base_context())


def test_all_14_rules_registered() -> None:
    """14 项规则全注册，且 ID 与 SPEC 3.2 M-G 清单一致（FR-19）。"""
    ids = rule_ids()
    assert len(ids) == 14
    assert ids == tuple(f"GATE-{index:03d}" for index in range(1, 15))
    assert all(rule.severity == "ERROR" for rule in all_rules())


async def test_compliant_candidate_passes_all_rules() -> None:
    """合规候选 + 合规 HOCON → 14 项规则全通过且引擎检查通过。"""
    result = await run_gate(
        base_plan(), COMPLIANT_HOCON, base_context(), engine_checker=LocalStubEngineChecker()
    )
    assert result.passed, [item.failure_line() for item in result.failures]
    assert len(result.results) == 14
    assert result.engine is not None and result.engine.ok


def test_rule_no_plaintext_credentials() -> None:
    """GATE-001：HOCON 中 `password=xxx` 字面量 → fail（占位符放行）。"""
    failed = check_one("GATE-001", base_plan(), 'sink { Jdbc { password = "s3cr3t-value" } }')
    assert not failed.passed
    # 摘要不含凭据值本身（AC-02）
    assert "s3cr3t-value" not in failed.summary

    plan = base_plan()
    plan["sink"]["password"] = "s3cr3t-value"
    assert not check_one("GATE-001", plan).passed
    assert check_one("GATE-001", base_plan()).passed


def test_rule_unsupported_connector() -> None:
    """GATE-002：白名单外连接器 → fail。"""
    plan = base_plan()
    plan["source"]["connector_type"] = "MONGODB"
    assert not check_one("GATE-002", plan).passed
    assert check_one("GATE-002", base_plan()).passed


def test_rule_unsupported_transform() -> None:
    """GATE-003：白名单外转换 → fail。"""
    plan = base_plan()
    plan["transforms"] = [{"type": "python_udf"}]
    assert not check_one("GATE-003", plan).passed


def test_rule_quality_contract_missing_sink() -> None:
    """GATE-004：缺 error_sink → fail。"""
    plan = base_plan()
    del plan["quality_contract"]["error_sink"]
    assert not check_one("GATE-004", plan).passed
    plan = base_plan()
    plan["quality_contract"]["error_sink"] = plan["quality_contract"]["valid_sink"]
    assert not check_one("GATE-004", plan).passed


def test_rule_quality_contract_routing() -> None:
    """GATE-005：routing 指向未声明的 sink → fail。"""
    plan = base_plan()
    plan["quality_contract"]["routing"] = [{"rule": "x", "sink": "not_declared"}]
    assert not check_one("GATE-005", plan).passed


def test_rule_quality_contract_traceability() -> None:
    """GATE-006：traceability 缺来源行标识列 → fail。"""
    plan = base_plan()
    del plan["quality_contract"]["traceability"]["source_row_id_column"]
    assert not check_one("GATE-006", plan).passed


def test_rule_quality_contract_predicates() -> None:
    """GATE-007：predicates 为空数组 → fail。"""
    plan = base_plan()
    plan["quality_contract"]["predicates"] = []
    assert not check_one("GATE-007", plan).passed


def test_rule_profile_topology() -> None:
    """GATE-008：端点角色错配 / 列清单为空 → fail。"""
    context = base_context()
    context["sink_profile"]["endpoint_role"] = "SOURCE"
    assert not check_one("GATE-008", base_plan(), context=context).passed

    context = base_context()
    context["source_profile"]["columns"] = []
    assert not check_one("GATE-008", base_plan(), context=context).passed


def test_rule_managed_file_source() -> None:
    """GATE-009：文件源未引用当前项目资产 → fail。"""
    plan = base_plan()
    plan["source"]["connector_type"] = "LOCAL_FILE"
    assert not check_one("GATE-009", plan).passed

    plan["file_source"] = {"file_asset_id": "44444444-4444-4444-4444-444444444444"}
    assert not check_one("GATE-009", plan).passed  # 不在当前项目白名单内

    context = base_context()
    context["file_asset_ids"] = ["44444444-4444-4444-4444-444444444444"]
    assert check_one("GATE-009", plan, context=context).passed


def test_rule_hdfs_contract() -> None:
    """GATE-010：HDFS 缺路径 / 格式非法 / 路径内嵌凭据 → fail。"""
    plan = base_plan()
    plan["source"]["connector_type"] = "HDFS"
    assert not check_one("GATE-010", plan).passed

    plan["hdfs"] = {"path": "/warehouse/orders", "format": "AVRO"}
    assert not check_one("GATE-010", plan).passed

    plan["hdfs"] = {"path": "hdfs://user:secret@host/warehouse", "format": "PARQUET"}
    assert not check_one("GATE-010", plan).passed

    plan["hdfs"] = {"path": "/warehouse/orders", "format": "PARQUET"}
    assert check_one("GATE-010", plan).passed


def test_rule_database_endpoints() -> None:
    """GATE-011：connection_id 不属于当前项目 → fail。"""
    plan = base_plan()
    plan["sink"]["connection_id"] = "55555555-5555-5555-5555-555555555555"
    assert not check_one("GATE-011", plan).passed


def test_rule_target_schema_mismatch() -> None:
    """GATE-012：列名缺失 / 类型族不一致 → fail。"""
    plan = base_plan()
    plan["sink"]["target_schema"]["columns"] = [{"name": "id", "type": "bigint"}]
    assert not check_one("GATE-012", plan).passed

    plan = base_plan()
    plan["sink"]["target_schema"]["columns"][1]["type"] = "text"  # 目标画像是 numeric
    assert not check_one("GATE-012", plan).passed

    assert check_one("GATE-012", base_plan()).passed


def test_rule_incremental_contract() -> None:
    """GATE-013：水印列缺失 / 类型非 TIMESTAMP → fail。"""
    plan = base_plan()
    plan["incremental"] = {"watermark_column": "not_exist", "lag_seconds": 60}
    assert not check_one("GATE-013", plan).passed

    plan = base_plan()
    plan["incremental"] = {"watermark_column": "amount", "lag_seconds": 60}
    assert not check_one("GATE-013", plan).passed

    plan = base_plan()
    plan["incremental"] = {"watermark_column": "updated_at", "lag_seconds": 60}
    assert check_one("GATE-013", plan).passed


def test_rule_row_llm_contract() -> None:
    """GATE-014：限速越界 / 主机不在白名单 → fail。"""
    plan = base_plan()
    plan["row_llm"] = {"rate_limit_per_second": 50, "allowed_hosts": ["api.openai.com"]}
    assert not check_one("GATE-014", plan).passed

    plan = base_plan()
    plan["row_llm"] = {"rate_limit_per_second": 5, "allowed_hosts": ["evil.example.com"]}
    assert not check_one("GATE-014", plan).passed

    plan = base_plan()
    plan["row_llm"] = {"rate_limit_per_second": 5, "allowed_hosts": ["api.deepseek.com"]}
    assert check_one("GATE-014", plan).passed
