"""PDP 9 条规则测试（T-12.1/12.2；S6 提示词第十二节 tests/harness）。

覆盖（FR-29）：9 条规则全注册 + 聚合逻辑（最严裁决 / 最高风险）+ DENY 清空审批要求。
"""
from __future__ import annotations

from etl_agent.harness.pdp.engine import (
    OUTCOME_RANK,
    RISK_APPROVALS,
    RISK_RANK,
    PdpEngine,
    PdpRequest,
)
from etl_agent.harness.pdp.rules import ALL_RULES, RULE_IDS
from etl_agent.harness.policy.package import build_package


def _request(**overrides: object) -> PdpRequest:
    """构造基线请求（同项目 / dev 环境 / 常规工具），按需覆盖字段。"""
    defaults: dict[str, object] = {
        "project_id": "p",
        "actor_project_id": "p",
        "environment": "dev",
        "tool_name": "pipeline.execute",
        "tool_version": "1.0.0",
    }
    defaults.update(overrides)
    return PdpRequest(**defaults)  # type: ignore[arg-type]


def _engine() -> PdpEngine:
    return PdpEngine(build_package())


def test_pdp_9_rules() -> None:
    """9 条规则全注册，且 rule_id 与 SPEC 8.2 清单一致（FR-29）。"""
    assert len(ALL_RULES) == 9
    assert RULE_IDS == (
        "HARNESS-P0-CROSS-PROJECT",
        "HARNESS-P0-PLAINTEXT-CREDENTIAL",
        "HARNESS-P0-DESTRUCTIVE-OP",
        "HARNESS-P0-SECRET-EXFIL",
        "HARNESS-P1-SENSITIVE-EXPORT",
        "HARNESS-P1-BUDGET-EXCEED",
        "HARNESS-P2-WRITE-PROD",
        "HARNESS-P2-SCHEMA-MISMATCH",
        "HARNESS-P3-ROUTINE",
    )
    assert [rule.risk_level for rule in ALL_RULES[:4]] == ["P0"] * 4


def test_pdp_aggregation_priority() -> None:
    """聚合取最严：DENY > DEFER > REQUIRE_APPROVAL > ALLOW；P0 > P1 > P2 > P3。"""
    assert OUTCOME_RANK["DENY"] < OUTCOME_RANK["REQUIRE_APPROVAL"] < OUTCOME_RANK["ALLOW"]
    assert RISK_RANK["P0"] < RISK_RANK["P3"]
    assert RISK_APPROVALS["P0"] == (["DATA_APPROVER", "SECURITY_APPROVER"], 2)
    assert RISK_APPROVALS["P1"][1] == 1 and RISK_APPROVALS["P3"][1] == 0


def test_pdp_cross_project_deny() -> None:
    """跨项目意图 → HARNESS-P0-CROSS-PROJECT / P0 / DENY（AC-12）。"""
    result = _engine().evaluate(_request(actor_project_id="other"))
    assert result.outcome == "DENY"
    assert result.risk_level == "P0"
    assert result.denied
    assert any(item["rule_id"] == "HARNESS-P0-CROSS-PROJECT" for item in result.rule_evidence)


def test_pdp_plaintext_credential_deny() -> None:
    """HOCON 含明文凭据 → DENY；只记字段名不记值（AC-02）。"""
    result = _engine().evaluate(
        _request(hocon='sink { Jdbc { password = "plain-secret" } }')
    )
    assert result.outcome == "DENY"
    evidence = [item for item in result.rule_evidence if item["outcome"] == "DENY"]
    assert evidence[0]["evidence"]["credential_field"] == "password"
    assert "plain-secret" not in str(result.to_dict())


def test_pdp_placeholder_password_allows() -> None:
    """`${}` 占位符不触发明文凭据规则（FR-68 口径同 GATE-001）。"""
    result = _engine().evaluate(_request(hocon='sink { Jdbc { password = "${SINK_PASSWORD}" } }'))
    assert result.outcome != "DENY"


def test_pdp_deny_no_approval() -> None:
    """DENY 清空审批要求：无放行通道（FR-29 / S6 禁止事项 19）。"""
    result = _engine().evaluate(
        _request(actor_project_id="other", environment="prod", classifications=("SECRET",))
    )
    assert result.outcome == "DENY"
    assert result.required_roles == []
    assert result.required_approvals == 0


def test_pdp_destructive_requires_two_approvals() -> None:
    """破坏性操作 → P0 / REQUIRE_APPROVAL，required_approvals=2（PRD 2.3-3 / AC-01）。"""
    result = _engine().evaluate(_request(command_type="CANCEL_EXECUTION"))
    assert result.outcome == "REQUIRE_APPROVAL"
    assert result.risk_level == "P0"
    assert result.required_approvals == 2
    assert set(result.required_roles) == {"DATA_APPROVER", "SECURITY_APPROVER"}


def test_pdp_sensitive_export_requires_security_approval() -> None:
    """敏感数据外发 → P1 / REQUIRE_APPROVAL（安全维度单审批）。"""
    result = _engine().evaluate(_request(classifications=("RESTRICTED",)))
    assert result.outcome == "REQUIRE_APPROVAL"
    assert result.risk_level == "P1"
    assert result.required_roles == ["SECURITY_APPROVER"]


def test_pdp_budget_exceed() -> None:
    """预算超限 → P1 / REQUIRE_APPROVAL，证据只含超限字段（不含业务数据）。"""
    result = _engine().evaluate(_request(budget={"max_records": 20_000_000}))
    budget_rules = [
        item for item in result.rule_evidence if item["rule_id"] == "HARNESS-P1-BUDGET-EXCEED"
    ]
    assert budget_rules and budget_rules[0]["evidence"]["exceeded"]["max_records"]["requested"] == 20_000_000


def test_pdp_write_prod_and_schema_mismatch() -> None:
    """生产写入 → P2；Schema 漂移 → P2；两者同时命中时风险取最严 P2。"""
    prod = _engine().evaluate(_request(environment="prod"))
    assert prod.outcome == "REQUIRE_APPROVAL" and prod.risk_level == "P2"
    assert prod.required_roles == ["DATA_APPROVER"]

    drift = _engine().evaluate(_request(target_schema_aligned=False))
    assert drift.risk_level == "P2"
    assert drift.required_approvals == 1


def test_pdp_routine_allow() -> None:
    """常规操作 → P3 / ALLOW，无需审批槽位（AC-05 ALLOW 路径）。"""
    result = _engine().evaluate(_request())
    assert result.outcome == "ALLOW"
    assert result.risk_level == "P3"
    assert result.required_roles == [] and result.required_approvals == 0


def test_pdp_secret_export_deny() -> None:
    """Secret 外发意图 → P0 / DENY（FR-06 凭据不外发）。"""
    result = _engine().evaluate(_request(secret_export=True))
    assert result.outcome == "DENY"
    assert result.risk_level == "P0"


def test_pdp_cross_project_beats_sensitive_export() -> None:
    """跨项目 + 敏感外发同时命中 → DENY（P0）压过 REQUIRE_APPROVAL（P1）。"""
    result = _engine().evaluate(_request(actor_project_id="other", classifications=("SECRET",)))
    assert result.outcome == "DENY"
    assert result.required_approvals == 0


def test_pdp_policy_package_params_used() -> None:
    """规则参数取自策略包：禁用该规则后不再命中（FR-30 策略驱动）。"""

    rules = [rule for rule in _engine().package.rules if rule.rule_id != "HARNESS-P2-WRITE-PROD"]
    engine = PdpEngine(build_package(rules=rules))
    result = engine.evaluate(_request(environment="prod"))
    assert all(item["rule_id"] != "HARNESS-P2-WRITE-PROD" for item in result.rule_evidence)
    assert result.risk_level != "P2"


def test_pdp_destructive_keyword_fallback_for_unknown_command() -> None:
    """未登记命令按关键字兜底判定破坏性（DROP/TRUNCATE），仍走四眼（FR-29）。"""
    result = _engine().evaluate(_request(command_type="DROP_TABLE"))
    assert any(item["rule_id"] == "HARNESS-P0-DESTRUCTIVE-OP" for item in result.rule_evidence)
