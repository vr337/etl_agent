"""M-L 运行时监督 — P3 预算预警规则（`SUPERVISION-P3-WARNING`）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（P3 预算预警：任一预算 > 80% → 软动作）、FR-46（动作 THROTTLE）
对应 AC：AC-08（预算水位可观测，未超限先预警）

判定：**任一**预算水位 ≥ 契约 `warning_ratio`（默认 0.8）→ THROTTLE / P3。

两个口径裁决（记录理由）：

1. **动作为 THROTTLE 而非 PAUSE**：提示词 §8.4 写「THROTTLE 或 PAUSE（软动作）」。
   选 THROTTLE——PAUSE 会停引擎作业，而此规则命中的前提是**尚未超预算**；
   在预算内主动停服属于过度减损（监督动作的边界是「收窄权限」而不是「提前停服」）。
   PAUSE 保留给运维在运行中心手动触发。
2. **水位按「最紧的一项」判定**：六项预算各自算水位，取最大者——
   命中证据里同时给出该项的实测值与阈值，避免「只说 80% 不说哪一项」的不可解释预警。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    RuleMatch,
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_THROTTLE

RULE_ID = "SUPERVISION-P3-WARNING"
RISK_LEVEL = "P3"
ACTIONS: tuple[str, ...] = (ACTION_THROTTLE,)


def _levels(
    contract: SupervisionContract, observation: SupervisionObservation
) -> list[tuple[str, float, float]]:
    """计算各项预算水位（项名 / 实测值 / 阈值）；观测缺失的项跳过。"""
    candidates: list[tuple[str, float | None, float]] = [
        ("records", float(observation.records) if observation.records is not None else None,
         float(contract.max_records)),
        ("bytes", float(observation.bytes_total) if observation.bytes_total is not None else None,
         float(contract.max_bytes)),
        (
            "duration_seconds",
            observation.duration_seconds,
            float(contract.max_duration_seconds),
        ),
        (
            "amplification_ratio",
            observation.amplification_ratio,
            contract.max_amplification_ratio,
        ),
        ("reject_rate", observation.reject_rate, contract.max_reject_rate),
    ]
    levels: list[tuple[str, float, float]] = []
    for name, value, threshold in candidates:
        if value is None or threshold <= 0:
            continue
        levels.append((name, value, threshold))
    return levels


def evaluate(
    contract: SupervisionContract, observation: SupervisionObservation
) -> RuleMatch | None:
    """任一预算水位达预警比例即命中（取最紧的一项作为证据）。"""
    levels = _levels(contract, observation)
    if not levels:
        return None
    name, value, threshold = max(levels, key=lambda item: item[1] / item[2])
    ratio = value / threshold
    if ratio < contract.warning_ratio:
        return None
    return RuleMatch(
        rule_id=RULE_ID,
        risk_level=RISK_LEVEL,
        actions=ACTIONS,
        observed={name: value, "watermark_ratio": round(ratio, 4)},
        threshold={name: threshold, "warning_ratio": contract.warning_ratio},
        detail=(
            f"{name} 水位 {ratio:.0%} 达到预警比例 {contract.warning_ratio:.0%}"
            f"（实测 {value} / 阈值 {threshold}，软动作 THROTTLE）"
        ),
    )


__all__ = ["ACTIONS", "RISK_LEVEL", "RULE_ID", "evaluate"]
