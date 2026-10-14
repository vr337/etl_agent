"""M-G 确定性门禁 — 门禁契约接口与 14 项规则注册表。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 7.1 章主流程时序图 / 第 8 章「性能 · 非 LLM 决策路径 P95 ≤20ms」
覆盖 FR：FR-19（14 项确定性静态契约校验）、FR-20（真实引擎检查前置于人工审批）
对应 AC：AC-04（不合规候选被拦截并进入有限修复）

判定口径（S5 提示词 11.5）：
- 每项规则返回 `RuleResult`；**任一规则 `severity=ERROR` 且 fail → 门禁不通过**；
- 全部规则均为进程内确定性计算（无 IO / 无 LLM），满足 SPEC 8 的非 LLM 决策 P95 指标；
- 门禁裁决**不用 LLM**（M-G 边界：不用 LLM 做门禁裁决）。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from etl_agent.gate.engine_check import EngineCheckResult

SEVERITY_ERROR = "ERROR"
SEVERITY_WARN = "WARN"

Plan = Mapping[str, Any]
Context = Mapping[str, Any]


@dataclass(frozen=True)
class RuleResult:
    """单项规则的判定结果（`summary` 必须是脱敏摘要，AC-02）。"""

    rule_id: str
    description: str
    severity: str
    passed: bool
    summary: str = ""

    @classmethod
    def of(cls, rule: GateRule, passed: bool, summary: str = "") -> RuleResult:
        """由规则元信息 + 判定结果构造（统一 severity 来源，避免规则各写一份）。"""
        return cls(
            rule_id=rule.rule_id,
            description=rule.description,
            severity=rule.severity,
            passed=passed,
            summary=summary,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "severity": self.severity,
            "passed": self.passed,
            "summary": self.summary,
        }

    def failure_line(self) -> str:
        """失败反馈行：只含规则 ID + 脱敏摘要（11.7：不反馈完整 HOCON）。"""
        return f"{self.rule_id}[{self.severity}] {self.description}: {self.summary}"


@runtime_checkable
class GateRule(Protocol):
    """门禁规则契约（S5 提示词 11.5）。"""

    rule_id: str
    description: str
    severity: str

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        """对候选 plan + HOCON 做确定性校验。"""
        ...


@dataclass
class GateResult:
    """门禁总判定：14 项规则 + 引擎检查的聚合结果。"""

    results: list[RuleResult]
    engine: EngineCheckResult | None = None

    @property
    def failures(self) -> list[RuleResult]:
        """未通过且 severity=ERROR 的规则（决定门禁不通过）。"""
        return [r for r in self.results if not r.passed and r.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[RuleResult]:
        return [r for r in self.results if not r.passed and r.severity == SEVERITY_WARN]

    @property
    def passed(self) -> bool:
        """全通过条件：无 ERROR 失败 且 引擎检查未失败（引擎缺失视为未执行，不阻断 S5 静态门禁）。"""
        engine_ok = self.engine is None or self.engine.ok
        return not self.failures and engine_ok

    def to_dict(self) -> dict[str, Any]:
        """门禁结果字典（脱敏：只有规则 ID / 描述 / 摘要，无候选正文）。"""
        return {
            "passed": self.passed,
            "rule_count": len(self.results),
            "failed_rule_ids": [r.rule_id for r in self.failures],
            "results": [r.to_dict() for r in self.results],
            "engine": None if self.engine is None else self.engine.to_dict(),
        }


# ============================================================
# 类型族归一（GATE-012 目标 Schema 对齐 / GATE-013 增量水印列类型）
# ============================================================

TYPE_FAMILIES: dict[str, frozenset[str]] = {
    "INTEGER": frozenset(
        {
            "int",
            "int2",
            "int4",
            "int8",
            "integer",
            "bigint",
            "smallint",
            "tinyint",
            "mediumint",
            "serial",
            "bigserial",
        }
    ),
    "DECIMAL": frozenset({"decimal", "numeric", "float", "float4", "float8", "double", "real", "money"}),
    "STRING": frozenset(
        {"text", "varchar", "char", "character", "string", "nvarchar", "clob", "uuid", "json", "jsonb"}
    ),
    "TIMESTAMP": frozenset(
        {
            "timestamp",
            "timestamptz",
            "datetime",
            "date",
            "time",
            "timestamp with time zone",
            "timestamp without time zone",
        }
    ),
    "BOOLEAN": frozenset({"bool", "boolean"}),
}


def type_family(raw_type: object) -> str | None:
    """归一化列类型到类型族（无法识别返回 None → 由调用方判定为不匹配）。"""
    if not isinstance(raw_type, str):
        return None
    normalized = raw_type.strip().lower()
    # 去掉精度括号与数组后缀：numeric(10,2) → numeric；varchar(255) → varchar
    normalized = normalized.split("(")[0].strip().rstrip("[]").strip()
    for family, members in TYPE_FAMILIES.items():
        if normalized in members:
            return family
    return None


def column_entries(profile: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """取画像的列清单（S4 吸收 4：`columns` 为 `{approximate_row_count, columns[]}` 包裹结构）。

    S5 生成期的画像由 `generation.nodes.generate` 归一化为裸数组后再传入，
    但门禁可能收到未归一化的原始 JSON，故此处兼容两种形态。
    """
    if not profile:
        return []
    raw = profile.get("columns")
    if isinstance(raw, dict):
        raw = raw.get("columns")
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


# ============================================================
# 14 项规则注册表（SPEC 3.2 M-G / FR-19）
# ============================================================
# 注册表实体位于 `gate.rules` 包；此处延迟导入，避免 rules → contracts → rules 的循环导入
# （规则实现必须继承本模块的 GateRule 契约，因此依赖方向只能单向）

RULE_REGISTRY_SIZE = 14


def all_rules() -> tuple[GateRule, ...]:
    """返回 14 项规则实例（顺序与 SPEC 3.2 M-G 清单一致）。"""
    from etl_agent.gate.rules import ALL_RULES

    return ALL_RULES


def rule_ids() -> tuple[str, ...]:
    """返回 14 项规则的 rule_id（GATE-001 ~ GATE-014）。"""
    from etl_agent.gate.rules import RULE_IDS

    return RULE_IDS

