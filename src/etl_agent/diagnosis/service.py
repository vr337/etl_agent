"""M-L 根因诊断 — 诊断服务（FR-47，AC-08 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 execution_runs
（failure_category / failure_stage / retryable / diagnosis）/ 第 8 章「可观测」
覆盖 FR：FR-47（可解释根因诊断：分类 + 阶段 + 可重试性 + 修复建议）
对应 AC：AC-08（硬中断后产出可解释根因）、AC-02（诊断不含错误栈正文）

**诊断三件套**落 `execution_runs`：`failure_category` / `failure_stage` / `retryable`，
人类可读的修复建议落 `diagnosis` 列（Text）。

**红线（禁止事项 16）**：诊断**只含摘要**——异常栈正文一律不落库、不进事件流。
本模块对传入的消息做两件事：① 截断（默认 240 字符）；② 剥掉 traceback 行
（`File "..."` / `at ...` / `Traceback` 这类行是栈正文的特征，出现即剔除）。
这不是「美化输出」，而是防止异常栈把文件路径、SQL 片段、甚至内联参数带进治理事实。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.diagnosis.classifiers import (
    CATEGORY_UNKNOWN,
    Classification,
    classify,
)
from etl_agent.execution.service import record_event
from etl_agent.infrastructure.models import ExecutionRun

# 诊断消息截断长度（只留摘要）
MESSAGE_LIMIT = 240

# 栈正文特征行（出现即剔除；S8 禁止事项 16）
_STACK_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*Traceback\b", re.IGNORECASE),
    re.compile(r'^\s*File "', re.IGNORECASE),
    re.compile(r"^\s*at\s+[\w.$]+\(.*\)\s*$"),
    re.compile(r"^\s*Caused by:", re.IGNORECASE),
    re.compile(r"^\s*\.{3}\s*\d+\s+more\s*$", re.IGNORECASE),
)


@dataclass(frozen=True)
class Diagnosis:
    """诊断结论（分类 / 阶段 / 可重试性 / 修复建议 / 判定来源）。"""

    failure_category: str
    failure_stage: str | None
    retryable: bool
    suggestion: str
    matched_by: str

    @property
    def diagnosis_text(self) -> str:
        """人类可读的修复建议（落 `execution_runs.diagnosis`）。"""
        return self.suggestion

    def as_evidence(self) -> dict[str, object]:
        return {
            "failure_category": self.failure_category,
            "failure_stage": self.failure_stage,
            "retryable": self.retryable,
            "suggestion": self.suggestion,
            "matched_by": self.matched_by,
        }


def strip_stack(message: str, *, limit: int = MESSAGE_LIMIT) -> str:
    """剔除栈正文行并截断（禁止事项 16：诊断只含摘要）。"""
    if not message:
        return ""
    kept = [
        line
        for line in message.splitlines()
        if not any(pattern.match(line) for pattern in _STACK_LINE_PATTERNS)
    ]
    collapsed = " ".join(" ".join(kept).split())
    return collapsed[:limit]


def build_diagnosis(
    *,
    failure_category: str | None = None,
    failure_stage: str | None = None,
    exception: BaseException | None = None,
    message: str = "",
) -> Diagnosis:
    """构造诊断结论（不落库；供纯函数用例与调用方预览）。"""
    classification: Classification = classify(
        failure_category=failure_category,
        exception=exception,
        message=strip_stack(message),
    )
    return Diagnosis(
        failure_category=classification.category,
        failure_stage=failure_stage,
        retryable=classification.retryable,
        suggestion=classification.suggestion,
        matched_by=classification.matched_by,
    )


async def apply_diagnosis(
    session: AsyncSession,
    run: ExecutionRun,
    *,
    failure_category: str | None = None,
    failure_stage: str | None = None,
    exception: BaseException | None = None,
    message: str = "",
) -> Diagnosis:
    """诊断并写入运行（三件套 + 建议 + 事件流留痕）。

    幂等：重复调用只会用更明确的结论覆盖（显式分类优先），不产生重复事件之外的副作用。
    """
    diagnosis = build_diagnosis(
        failure_category=failure_category,
        failure_stage=failure_stage,
        exception=exception,
        message=message,
    )
    run.failure_category = diagnosis.failure_category
    run.failure_stage = diagnosis.failure_stage
    run.retryable = diagnosis.retryable
    run.diagnosis = diagnosis.diagnosis_text
    await session.flush()
    await record_event(
        session,
        run.id,
        level="ERROR" if not diagnosis.retryable else "WARN",
        event_type="execution.diagnosis",
        message=(
            f"根因诊断 {diagnosis.failure_category}"
            f"（阶段 {diagnosis.failure_stage or '未知'}，"
            f"{'可重试' if diagnosis.retryable else '不可重试'}）：{diagnosis.suggestion}"
        ),
        stage=diagnosis.failure_stage or "diagnose",
    )
    return diagnosis


async def diagnose_run(
    session: AsyncSession, run: ExecutionRun, *, message: str = ""
) -> Diagnosis:
    """按运行已有事实（failure_category / failure_stage / diagnosis）复核并补全诊断。

    用途：运行已在别处落了失败分类（如 S7 的引擎失败路径），运行中心需要
    可解释建议时调用本函数补全——不重写已有分类，只补建议与可重试性。
    """
    return await apply_diagnosis(
        session,
        run,
        failure_category=run.failure_category or CATEGORY_UNKNOWN,
        failure_stage=run.failure_stage,
        message=message or run.diagnosis or "",
    )


__all__ = [
    "MESSAGE_LIMIT",
    "Diagnosis",
    "apply_diagnosis",
    "build_diagnosis",
    "diagnose_run",
    "strip_stack",
]
