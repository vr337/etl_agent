"""M-F 生成引擎 — 状态节点：意图解析 + 澄清中断。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 7.1 章主流程时序图 / 第 6.1 章 API-05/06
覆盖 FR：FR-16（参数缺失转 WAITING_INPUT）、FR-17（interrupt 提问 + 同 thread 恢复）
对应 AC：AC-03（LangGraph 中断与恢复）

实现要点：
- 每次只挂起**一个问题**（缺失字段的第一个），恢复后落位并返回；
  若仍有缺失，条件边会再次进入本节点产生新的中断——
  这样任一时刻只有一个待恢复中断，`Command(resume=...)` 的对应关系始终唯一。
- 中断值只含静态问题文案与字段名，不含用户输入（可安全落业务库 pending_question）。
"""
from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from etl_agent.generation.state import (
    MISSING_QUESTIONS,
    STATUS_RUNNING,
    GenerationDeps,
    GenerationState,
    apply_answer,
    missing_fields,
)


async def intent_node(state: GenerationState, deps: GenerationDeps) -> dict[str, Any]:
    """意图解析节点：缺参数则挂起澄清，否则放行到生成。

    Returns:
        dict: 状态增量（澄清回答落位 / 清空挂起问题 / 置 RUNNING）。
    """
    missing = missing_fields(state)
    if not missing:
        return {"pending_question": None, "status": STATUS_RUNNING}

    field = missing[0]
    answer = interrupt({"question": MISSING_QUESTIONS[field], "field": field})
    # 恢复分支：answer 为本次中断的 resume 值；落位后立即返回，
    # 是否仍需澄清交由条件边判定（不在节点内循环，避免同节点多次 interrupt）
    return {
        **apply_answer(field, answer),
        "pending_question": None,
        "status": STATUS_RUNNING,
    }
