"""M-J Harness 安全执行内核 — PDP 引擎（9 条确定性规则 + 最严聚合）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J（策略决策点）/ 第 8 章「性能 · 非 LLM 决策路径 P95 ≤20ms」
覆盖 FR：FR-29（9 条确定性规则，输出 P0–P3 + ALLOW/DENY/REQUIRE_APPROVAL/DEFER；DENY>DEFER>REQUIRE_APPROVAL>ALLOW、P0>P1>P2>P3 取最严；DENY 清空审批要求；只存规则证据）
对应 AC：AC-01（职责分离）、AC-12（跨项目 DENY）

三条红线：
1. **纯进程内确定性计算**（无 IO、无 LLM）：满足 SPEC 8「非 LLM 决策路径 P95 ≤20ms」；
2. **DENY 清空审批要求**：DENY 是终局裁决，不存在人工放行通道（FR-29 / 禁止事项 19）；
3. **只落规则证据**：`rule_evidence` 只含 rule_id / 风险 / 命中事实，不含 HOCON 正文与 Prompt（AC-02）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from etl_agent.harness.policy.package import PolicyPackage, package_id

RISK_LEVELS: tuple[str, ...] = ("P0", "P1", "P2", "P3")
OUTCOMES: tuple[str, ...] = ("ALLOW", "DENY", "REQUIRE_APPROVAL", "DEFER")

# 风险越靠前越严（P0 最严）；裁决越靠前越严（DENY 最严）
RISK_RANK: dict[str, int] = {level: index for index, level in enumerate(RISK_LEVELS)}
OUTCOME_RANK: dict[str, int] = {"DENY": 0, "DEFER": 1, "REQUIRE_APPROVAL": 2, "ALLOW": 3}

# 风险等级 → 审批槽位与票数（S6 提示词 8.4：P0=2，P1/P2=1，P3=0）
RISK_APPROVALS: dict[str, tuple[list[str], int]] = {
    "P0": (["DATA_APPROVER", "SECURITY_APPROVER"], 2),
    "P1": (["SECURITY_APPROVER"], 1),
    "P2": (["DATA_APPROVER"], 1),
    "P3": ([], 0),
}


@dataclass(frozen=True)
class PdpRequest:
    """PDP 输入：只含治理事实与指纹，不含凭据与 Prompt 正文。"""

    project_id: str
    actor_project_id: str | None
    environment: str
    tool_name: str
    tool_version: str
    pipeline_version_id: str | None = None
    artifact_sha256: str | None = None
    command_type: str | None = None
    side_effect_type: str | None = None
    resource_ids: tuple[str, ...] = ()
    hocon: str | None = None
    classifications: tuple[str, ...] = ()
    budget: Mapping[str, Any] | None = None
    target_schema_aligned: bool | None = None
    secret_export: bool = False


@dataclass(frozen=True)
class RuleOutcome:
    """单条规则的命中结果（未命中返回 None）。"""

    rule_id: str
    risk_level: str
    outcome: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "risk_level": self.risk_level,
            "outcome": self.outcome,
            "evidence": self.evidence,
        }


@runtime_checkable
class PdpRule(Protocol):
    """PDP 规则契约（参数来自签名策略包，阈值不硬编码在规则里）。"""

    rule_id: str
    version: str
    risk_level: str

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        """命中则返回 `RuleOutcome`，未命中返回 None。"""
        ...


@dataclass(frozen=True)
class PolicyDecisionResult:
    """聚合后的策略裁决（落 `harness_policy_decisions`）。"""

    outcome: str
    risk_level: str
    rule_evidence: list[dict[str, Any]]
    required_roles: list[str]
    required_approvals: int
    policy_package_id: str
    policy_package_sha256: str

    @property
    def denied(self) -> bool:
        return self.outcome == "DENY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "risk_level": self.risk_level,
            "rule_evidence": self.rule_evidence,
            "required_roles": self.required_roles,
            "required_approvals": self.required_approvals,
            "policy_package_id": self.policy_package_id,
            "policy_package_sha256": self.policy_package_sha256,
        }


class PdpEngine:
    """策略决策点：按签名策略包逐条评估并取最严聚合（FR-29）。"""

    def __init__(
        self, package: PolicyPackage | Mapping[str, Any], rules: Sequence[PdpRule] | None = None
    ) -> None:
        """接受已验签的 `PolicyPackage` 或清单字典（后者经 `package_from_mapping` 包装）。"""
        if isinstance(package, PolicyPackage):
            resolved = package
        else:
            from etl_agent.harness.policy.package import package_from_mapping

            resolved = package_from_mapping(package)
        if rules is None:
            from etl_agent.harness.pdp.rules import ALL_RULES

            rules = ALL_RULES
        self._package = resolved
        self._rules = tuple(rules)

    @property
    def package(self) -> PolicyPackage:
        return self._package

    def evaluate(self, request: PdpRequest) -> PolicyDecisionResult:
        """评估并聚合（DENY > DEFER > REQUIRE_APPROVAL > ALLOW；P0 > P1 > P2 > P3）。"""
        matched: list[RuleOutcome] = []
        for rule in self._rules:
            # 策略包未登记的规则不参与评估：策略可用「移除条目」整体关闭某条规则（FR-30）
            if self._package.rule(rule.rule_id) is None:
                continue
            outcome = rule.evaluate(request, self._package.params_of(rule.rule_id))
            if outcome is not None:
                matched.append(outcome)
        if not matched:
            matched.append(
                RuleOutcome("HARNESS-P3-ROUTINE", "P3", "ALLOW", {"reason": "无规则命中"})
            )

        # 取最严：裁决按 OUTCOME_RANK、风险按 RISK_RANK（两维独立取最严，SPEC 3.2 M-J）
        strictest_outcome = min(matched, key=lambda item: OUTCOME_RANK[item.outcome]).outcome
        risk_level = min(matched, key=lambda item: RISK_RANK[item.risk_level]).risk_level

        if strictest_outcome == "DENY":
            # DENY 清空审批要求：无人工放行通道（FR-29 / 禁止事项 19）
            roles: list[str] = []
            approvals = 0
        else:
            roles, approvals = RISK_APPROVALS[risk_level]
            for item in matched:
                override = self._package.params_of(item.rule_id).get("required_approvals")
                if isinstance(override, int):
                    approvals = max(approvals, override)
            if approvals > len(roles):
                roles = list(roles) + ["DATA_APPROVER", "SECURITY_APPROVER"]
                roles = list(dict.fromkeys(roles))[:approvals]

        return PolicyDecisionResult(
            outcome=strictest_outcome,
            risk_level=risk_level,
            rule_evidence=[item.to_dict() for item in matched],
            required_roles=roles,
            required_approvals=approvals,
            policy_package_id=package_id(self._package),
            policy_package_sha256=self._package.package_sha256,
        )
