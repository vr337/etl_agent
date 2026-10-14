"""M-F 生成引擎 — LangGraph 状态定义与节点依赖容器。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 5.3 章三层状态隔离 / ADR-03
覆盖 FR：FR-16（运行生命周期）、FR-17（澄清中断与恢复）、FR-67/68（对话与工作流状态落 etl_checkpoint）
对应 AC：AC-03（中断与恢复）、AC-13（三层状态隔离）

状态隔离边界（SPEC 5.3）：
- 本 TypedDict 即 Workflow State 的载荷，随 LangGraph Checkpoint 落在 `etl_checkpoint` 库；
- 图内**不含凭据**（FR-68）：连接只以 `connection_id` 引用，运行期变量在 Worker 侧物化；
- `requirement` / `candidate` 属于对话与候选正文，**只允许**存在于 Checkpoint 库，
  业务库 `agent_runs` 只保留计数 / 耗时 / 非敏感诊断（FR-67）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.gate.engine_check import EngineChecker
from etl_agent.llm.provider import LLMProvider

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_WAITING_INPUT = "WAITING_INPUT"
STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"
STATUS_FAILED = "FAILED"

FAILURE_BUDGET_EXHAUSTED = "LLM_BUDGET_EXHAUSTED"

# 澄清问题文案：静态文本，不含用户输入（可安全落业务库 pending_question）
MISSING_QUESTIONS: dict[str, str] = {
    "requirement": "请补充本次 ETL 的自然语言需求描述。",
    "source_profile_ids": "请提供来源端 Profile ID（多个以逗号分隔）。",
    "sink_profile_ids": "请提供目标端 Profile ID（多个以逗号分隔）。",
}


class GenerationState(TypedDict, total=False):
    """生成状态机载荷（FR-68 Workflow State）。"""

    run_id: str
    project_id: str
    pipeline_version_id: str
    requirement: str
    source_profile_ids: list[str]
    sink_profile_ids: list[str]
    file_asset_ids: list[str]
    source_profile: dict[str, Any]
    sink_profile: dict[str, Any]
    candidate: dict[str, Any]
    candidate_count: int
    repair_count: int
    gate_result: dict[str, Any] | None
    repair_feedback: str | None
    pending_question: str | None
    status: str
    failure_category: str | None


@dataclass
class GenerationDeps:
    """节点依赖（一次图调用内共享；不进入状态，故不落 Checkpoint）。"""

    settings: Settings
    provider: LLMProvider
    session: AsyncSession
    engine_checker: EngineChecker


def missing_fields(state: GenerationState) -> list[str]:
    """计算缺失的必要参数（按固定顺序，保证澄清问题顺序确定）。"""
    missing: list[str] = []
    if not (state.get("requirement") or "").strip():
        missing.append("requirement")
    if not state.get("source_profile_ids"):
        missing.append("source_profile_ids")
    if not state.get("sink_profile_ids"):
        missing.append("sink_profile_ids")
    return missing


def apply_answer(field: str, answer: Any) -> dict[str, Any]:
    """把澄清回答落位到对应字段（FR-17：用户回答后从同一 thread 恢复）。

    Profile ID 类回答按逗号 / 空白切分并做 UUID 规范化——非法片段直接丢弃，
    由后续画像加载以 `BIZ_404_PROFILE_NOT_FOUND` 拒绝（不在这里做业务判决）。
    """
    if field == "requirement":
        return {"requirement": str(answer).strip()}
    tokens = str(answer).replace(",", " ").split()
    normalized: list[str] = []
    for token in tokens:
        try:
            normalized.append(str(uuid.UUID(token)))
        except ValueError:
            continue
    return {field: normalized}
