"""M-F 生成引擎 — 生成遥测（只记计数 / 耗时 / 非敏感诊断）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 5.2 章 agent_runs（generation_* 字段）
覆盖 FR：FR-17（生成遥测只记次数/耗时/非敏感诊断）、FR-67（Prompt 与候选正文不落业务库）
对应 AC：AC-13（业务库无 Prompt / 候选正文）

红线（PRD 10.5-2 / S5 提示词 11.4）：
**禁止落库** `requirement` 全文、`etl_plan` 正文、`hocon` 正文、Prompt 内容。
本模块是遥测写入的唯一出口，任何诊断字典都先经 `sanitize_diagnosis` 过滤禁用键，
再从物理上保证业务库不会出现候选正文。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from etl_agent.infrastructure.models import AgentRun

# 业务库禁写键（FR-67）：出现即剔除，且测试断言其永不出现
FORBIDDEN_DIAGNOSIS_KEYS = frozenset(
    {"requirement", "prompt", "messages", "etl_plan", "hocon", "candidate", "content"}
)


@dataclass(frozen=True)
class GenerationTelemetry:
    """单次生成的遥测快照（字段与 `agent_runs.generation_*` 一一对应）。"""

    backend: str
    model: str
    candidate_count: int
    repair_count: int
    duration_ms: int
    failure_category: str | None = None

    def apply_to(self, run: AgentRun) -> None:
        """写入 AgentRun 的 6 个非敏感遥测字段（不触碰任何正文字段）。"""
        run.generation_backend = self.backend
        run.generation_model = self.model
        run.generation_candidate_count = self.candidate_count
        run.generation_repair_count = self.repair_count
        run.generation_duration_ms = self.duration_ms
        run.generation_diagnosis = self.diagnosis()

    def diagnosis(self) -> dict[str, Any]:
        """非敏感诊断字典（只有分类与计数，无正文）。"""
        payload: dict[str, Any] = {
            "candidate_count": self.candidate_count,
            "repair_count": self.repair_count,
            "duration_ms": self.duration_ms,
        }
        if self.failure_category:
            payload["failure_category"] = self.failure_category
        return sanitize_diagnosis(payload)


def sanitize_diagnosis(diagnosis: dict[str, Any] | None) -> dict[str, Any]:
    """剔除业务库禁写键（FR-67 最后一道闸）。

    即使上游误传正文，也不会写进 `agent_runs.generation_diagnosis`。
    """
    if not diagnosis:
        return {}
    return {
        key: value
        for key, value in diagnosis.items()
        if key.lower() not in FORBIDDEN_DIAGNOSIS_KEYS
    }
