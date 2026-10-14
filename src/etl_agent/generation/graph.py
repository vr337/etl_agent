"""M-F 生成引擎 — LangGraph 状态机装配。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 7.1 章主流程时序图 / ADR-03
覆盖 FR：FR-17（澄清中断与恢复）、FR-18（生成）、FR-19/20（门禁）、FR-21（有限修复）
对应 AC：AC-03（LangGraph 中断与恢复）、AC-04（门禁 + 有限修复）

节点顺序（S5 提示词 11.3）：

```
START → intent ──缺参数──→ interrupt(挂起) → 恢复 → intent
              └─参数齐──→ generate → gate ──通过──→ END（WAITING_APPROVAL，S6 接管）
                                       └─不通过──→ repair ──预算内──→ generate
                                                     └─预算耗尽──→ END（FAILED）
```

interrupt / resume 语义：`interrupt()` 在节点内挂起并持久化到 Checkpoint；
`Command(resume=...)` 以同一 `thread_id` 跨请求恢复（FR-17 / AC-03）。
每次只挂起一个中断，故 resume 值与中断一一对应。
"""
from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from etl_agent.generation.nodes.gate import gate_node
from etl_agent.generation.nodes.generate import generate_node
from etl_agent.generation.nodes.intent import intent_node
from etl_agent.generation.nodes.repair import repair_node
from etl_agent.generation.state import (
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    GenerationDeps,
    GenerationState,
    missing_fields,
)


def route_after_intent(state: GenerationState) -> str:
    """意图后分支：仍缺参数 → 继续澄清；参数齐备 → 生成。"""
    return "intent" if missing_fields(state) else "generate"


def route_after_gate(state: GenerationState) -> str:
    """门禁后分支：通过 → 结束（等审批）；不通过 → 有限修复。"""
    return "end" if state.get("status") == STATUS_WAITING_APPROVAL else "repair"


def route_after_repair(state: GenerationState) -> str:
    """修复后分支：预算内 → 再生成；预算耗尽（FAILED）→ 结束。"""
    return "generate" if state.get("status") == STATUS_RUNNING else "end"


def build_graph(
    deps: GenerationDeps, checkpointer: Any
) -> CompiledStateGraph[GenerationState, Any, GenerationState, GenerationState]:
    """编译生成状态机（每次运行注入节点依赖与 Checkpointer）。

    Args:
        deps: 节点依赖（配置 / Provider / 会话 / 引擎检查器）。
        checkpointer: LangGraph Checkpointer；生产为连 `etl_checkpoint` 库的
            `AsyncPostgresSaver`，测试为 `InMemorySaver`（ADR-03 双库分离）。

    Returns:
        CompiledStateGraph: 可 `ainvoke` 的状态机（interrupt 需 Checkpointer 支持）。
    """
    builder = StateGraph(GenerationState)
    builder.add_node("intent", partial(intent_node, deps=deps))
    builder.add_node("generate", partial(generate_node, deps=deps))
    builder.add_node("gate", partial(gate_node, deps=deps))
    builder.add_node("repair", partial(repair_node, deps=deps))

    builder.add_edge(START, "intent")
    builder.add_conditional_edges(
        "intent", route_after_intent, {"intent": "intent", "generate": "generate"}
    )
    builder.add_edge("generate", "gate")
    builder.add_conditional_edges("gate", route_after_gate, {"end": END, "repair": "repair"})
    builder.add_conditional_edges("repair", route_after_repair, {"generate": "generate", "end": END})
    return builder.compile(checkpointer=checkpointer)
