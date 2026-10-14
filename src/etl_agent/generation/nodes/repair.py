"""M-F 生成引擎 — 状态节点：有限修复（预算 ≤3，含初始候选）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G（候选预算）/ 第 3.2 章 M-F / 第 8 章「性能 · LLM 生成」
覆盖 FR：FR-21（格式错误 / 门禁错误 / 引擎检查共享预算，最多 3 份候选，耗尽即确定性失败）
对应 AC：AC-04（有限修复）

判定口径（S5 提示词 11.7）：预算**含初始候选**——已生成 `max_candidates` 份后不再重试，
直接把运行置为 FAILED 并记录 `LLM_BUDGET_EXHAUSTED` 诊断；反馈已由门禁节点写入状态。
"""
from __future__ import annotations

from typing import Any

from etl_agent.gate.service import resolve_budget
from etl_agent.generation.state import (
    FAILURE_BUDGET_EXHAUSTED,
    STATUS_FAILED,
    STATUS_RUNNING,
    GenerationDeps,
    GenerationState,
)


async def repair_node(state: GenerationState, deps: GenerationDeps) -> dict[str, Any]:
    """修复节点：预算内放行再生成，预算耗尽则确定性失败。

    Returns:
        dict: 状态增量（修复计数 +1 且 RUNNING；或 FAILED + 失败分类）。
    """
    budget = resolve_budget(deps.settings)
    generated = state.get("candidate_count", 0)
    if budget.exhausted(generated):
        # 预算耗尽即确定性失败（FR-21）：不抛异常，由服务层统一落库并对外报错
        return {"status": STATUS_FAILED, "failure_category": FAILURE_BUDGET_EXHAUSTED}
    return {
        "repair_count": state.get("repair_count", 0) + 1,
        "status": STATUS_RUNNING,
    }
