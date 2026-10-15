"""M-L 运行时监督 — 监督契约与 6 条确定性规则（FR-45，AC-08 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 runtime_supervision_snapshots /
第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（冻结预算 + 6 条确定性规则 + 快照 `snapshot_sha256`）、FR-46（减损动作由规则决定）
对应 AC：AC-08（超预算 → 硬中断 + 隔离）

**冻结预算**（SPEC 5.2，不可被运行期放宽）：

| 项 | 默认 | 语义 |
|----|------|------|
| `max_records` | 10,000,000 | 最大记录数 |
| `max_bytes` | 10 GiB | 最大字节数 |
| `max_duration_seconds` | 3600 | 最长时长 |
| `max_amplification_ratio` | 2.0 | 输出放大比上限 |
| `max_reject_rate` | 0.5 | 拒绝率上限 |
| `min_quality_rows` | 100 | 最低质量行数（质量判定下限） |
| `warning_ratio` | 0.8 | 预算预警比例 |

**只收窄不放宽**（S8 禁止事项 14 / SPEC 8）：`SupervisionContract.resolve()` 对调用方传入的
预算做**单向夹取**——上界类项取 `min(传入, 默认)`，下界类项（`min_quality_rows`）取
`max(传入, 默认)`。任何试图放宽冻结预算的传参都会被静默夹回默认值，
这是「监督动作只能收窄权限，不能扩权」在契约层的实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 冻结预算键（SPEC 5.2）
KEY_MAX_RECORDS = "max_records"
KEY_MAX_BYTES = "max_bytes"
KEY_MAX_DURATION_SECONDS = "max_duration_seconds"
KEY_MAX_AMPLIFICATION_RATIO = "max_amplification_ratio"
KEY_MAX_REJECT_RATE = "max_reject_rate"
KEY_MIN_QUALITY_ROWS = "min_quality_rows"
KEY_WARNING_RATIO = "warning_ratio"

# SPEC 5.2 冻结预算默认值（不可放宽）
DEFAULT_BUDGET: dict[str, int | float] = {
    KEY_MAX_RECORDS: 10_000_000,
    KEY_MAX_BYTES: 10 * 1024**3,
    KEY_MAX_DURATION_SECONDS: 3600,
    KEY_MAX_AMPLIFICATION_RATIO: 2.0,
    KEY_MAX_REJECT_RATE: 0.5,
    KEY_MIN_QUALITY_ROWS: 100,
    KEY_WARNING_RATIO: 0.8,
}

# 下界类项：越严越大（其余为「越严越小」）
_LOWER_BOUND_KEYS: frozenset[str] = frozenset({KEY_MIN_QUALITY_ROWS})

# 6 条规则 ID（验收命令 9 核对项；顺序即文档顺序，不表示优先级）
RULE_IDS: tuple[str, ...] = (
    "SUPERVISION-P0-RECORD",
    "SUPERVISION-P0-BYTE",
    "SUPERVISION-P1-TIMEOUT",
    "SUPERVISION-P0-AMPLIFICATION",
    "SUPERVISION-P1-REJECT-RATE",
    "SUPERVISION-P3-WARNING",
)


@dataclass(frozen=True)
class SupervisionContract:
    """监督契约（冻结预算的强类型视图）。"""

    max_records: int
    max_bytes: int
    max_duration_seconds: int
    max_amplification_ratio: float
    max_reject_rate: float
    min_quality_rows: int
    warning_ratio: float

    @classmethod
    def frozen(cls) -> SupervisionContract:
        """SPEC 冻结预算默认值。"""
        return cls(
            max_records=int(DEFAULT_BUDGET[KEY_MAX_RECORDS]),
            max_bytes=int(DEFAULT_BUDGET[KEY_MAX_BYTES]),
            max_duration_seconds=int(DEFAULT_BUDGET[KEY_MAX_DURATION_SECONDS]),
            max_amplification_ratio=float(DEFAULT_BUDGET[KEY_MAX_AMPLIFICATION_RATIO]),
            max_reject_rate=float(DEFAULT_BUDGET[KEY_MAX_REJECT_RATE]),
            min_quality_rows=int(DEFAULT_BUDGET[KEY_MIN_QUALITY_ROWS]),
            warning_ratio=float(DEFAULT_BUDGET[KEY_WARNING_RATIO]),
        )

    @classmethod
    def resolve(cls, raw: dict[str, Any] | None) -> SupervisionContract:
        """按「只收窄不放宽」解析传入预算（类型非法 / 缺项一律回落默认值）。"""
        base = cls.frozen()
        if not isinstance(raw, dict):
            return base
        values: dict[str, int | float] = {}
        for key, default in DEFAULT_BUDGET.items():
            candidate = raw.get(key)
            if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
                values[key] = default
                continue
            if key in _LOWER_BOUND_KEYS:
                values[key] = max(float(candidate), float(default))
            else:
                values[key] = min(float(candidate), float(default))
        return cls(
            max_records=int(values[KEY_MAX_RECORDS]),
            max_bytes=int(values[KEY_MAX_BYTES]),
            max_duration_seconds=int(values[KEY_MAX_DURATION_SECONDS]),
            max_amplification_ratio=float(values[KEY_MAX_AMPLIFICATION_RATIO]),
            max_reject_rate=float(values[KEY_MAX_REJECT_RATE]),
            min_quality_rows=int(values[KEY_MIN_QUALITY_ROWS]),
            warning_ratio=float(values[KEY_WARNING_RATIO]),
        )

    def as_dict(self) -> dict[str, int | float]:
        """回写为可落 JSON 列的形态（用于准备单 `supervision_contract` 对账）。"""
        return {
            KEY_MAX_RECORDS: self.max_records,
            KEY_MAX_BYTES: self.max_bytes,
            KEY_MAX_DURATION_SECONDS: self.max_duration_seconds,
            KEY_MAX_AMPLIFICATION_RATIO: self.max_amplification_ratio,
            KEY_MAX_REJECT_RATE: self.max_reject_rate,
            KEY_MIN_QUALITY_ROWS: self.min_quality_rows,
            KEY_WARNING_RATIO: self.warning_ratio,
        }


@dataclass(frozen=True)
class SupervisionObservation:
    """一次监督观测（**只含聚合指标**，不含日志正文与原始数据，SPEC 8「可观测」）。"""

    records: int | None = None
    bytes_total: int | None = None
    duration_seconds: float | None = None
    amplification_ratio: float | None = None
    reject_rate: float | None = None
    quality_rows: int | None = None

    def as_observed(self) -> dict[str, Any]:
        """快照 `observed` 字段（键名对齐 SPEC 5.2 的聚合指标口径）。"""
        return {
            "records": self.records,
            "bytes": self.bytes_total,
            "duration_seconds": self.duration_seconds,
            "amplification_ratio": self.amplification_ratio,
            "reject_rate": self.reject_rate,
            "quality_rows": self.quality_rows,
        }


@dataclass(frozen=True)
class RuleMatch:
    """规则命中证据（只含规则 ID / 风险 / 动作 / 聚合值 / 阈值，SPEC 5.2 `rule_matches`）。"""

    rule_id: str
    risk_level: str
    actions: tuple[str, ...]
    observed: dict[str, Any]
    threshold: dict[str, Any]
    detail: str

    @property
    def action(self) -> str:
        """主动作（多动作规则取首位；其余动作由 `mitigation.plan_mitigation` 展开）。"""
        return self.actions[0]

    def as_evidence(self) -> dict[str, Any]:
        """落库形态（规则证据，不含原始数据）。"""
        return {
            "rule_id": self.rule_id,
            "risk_level": self.risk_level,
            "actions": list(self.actions),
            "observed": dict(self.observed),
            "threshold": dict(self.threshold),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SupervisionOutcome:
    """一次监督评估结果（快照写入与动作分发的输入）。"""

    action: str
    risk_level: str
    matches: tuple[RuleMatch, ...] = field(default_factory=tuple)
    contract: SupervisionContract = field(default_factory=SupervisionContract.frozen)

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(item.rule_id for item in self.matches)

    def evidence(self) -> list[dict[str, Any]]:
        return [item.as_evidence() for item in self.matches]


def all_rules() -> tuple[Any, ...]:
    """规则注册表（延迟导入：规则模块依赖本模块的契约类型，模块级导入会成环）。"""
    from etl_agent.supervision.rules import ALL_RULES

    return tuple(ALL_RULES)


def evaluate(
    contract: SupervisionContract,
    observation: SupervisionObservation,
    *,
    rules: tuple[Any, ...] | None = None,
) -> SupervisionOutcome:
    """按契约评估全部规则并聚合出最严动作（FR-45）。

    聚合规则（提示词 §8.4）：最严动作胜出（CANCEL > QUARANTINE > PAUSE > THROTTLE > CONTINUE）、
    最严风险胜出（P0 > P1 > P2 > P3）。未命中任何规则 → CONTINUE / risk=None。
    """
    from etl_agent.supervision.mitigation import plan_mitigation

    active = rules if rules is not None else all_rules()
    matches: list[RuleMatch] = []
    for rule in active:
        match = rule.evaluate(contract, observation)
        if match is not None:
            matches.append(match)
    plan = plan_mitigation(matches)
    return SupervisionOutcome(
        action=plan.action,
        risk_level=plan.risk_level or "",
        matches=tuple(matches),
        contract=contract,
    )


__all__ = [
    "DEFAULT_BUDGET",
    "KEY_MAX_AMPLIFICATION_RATIO",
    "KEY_MAX_BYTES",
    "KEY_MAX_DURATION_SECONDS",
    "KEY_MAX_RECORDS",
    "KEY_MAX_REJECT_RATE",
    "KEY_MIN_QUALITY_ROWS",
    "KEY_WARNING_RATIO",
    "RULE_IDS",
    "RuleMatch",
    "SupervisionContract",
    "SupervisionObservation",
    "SupervisionOutcome",
    "all_rules",
    "evaluate",
]
