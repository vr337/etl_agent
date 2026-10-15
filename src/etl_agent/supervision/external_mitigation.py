"""M-L 运行时监督 — 外部减损动作独立重试（FR-46）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-46（外部减损动作独立可重试：NOT_REQUIRED/PENDING/APPLYING/APPLIED/FAILED）
对应 AC：AC-08（外部动作失败不影响监督快照与诊断）

状态机：

```
NOT_REQUIRED ──(无外部动作)────────────────────────────► 终态
      │
      └─(有外部动作)─► PENDING ─► APPLYING ─► APPLIED（成功）
                                    │
                                    └─(失败且未耗尽重试)─► PENDING（下一轮）
                                    └─(失败且耗尽重试)───► FAILED（终态）
```

**独立性的实现**（S8 禁止事项 18）：本模块**只返回结果，不抛异常**——
调用方拿到 `FAILED` 后照样写快照、照样出诊断。外部动作是执行手段，
手段失败不能让「运行已被判定超预算」这一治理事实消失。
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.service import record_event

STATUS_NOT_REQUIRED = "NOT_REQUIRED"
STATUS_PENDING = "PENDING"
STATUS_APPLYING = "APPLYING"
STATUS_APPLIED = "APPLIED"
STATUS_FAILED = "FAILED"

ALL_STATUSES: tuple[str, ...] = (
    STATUS_NOT_REQUIRED,
    STATUS_PENDING,
    STATUS_APPLYING,
    STATUS_APPLIED,
    STATUS_FAILED,
)

TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_NOT_REQUIRED, STATUS_APPLIED, STATUS_FAILED})

# 重试预算默认 3 次（提示词 §8.9）
DEFAULT_MAX_ATTEMPTS = 3

# 错误摘要截断（只回分类，不含错误栈正文，S8 禁止事项 16）
_ERROR_SUMMARY_LIMIT = 160


@dataclass(frozen=True)
class ExternalMitigationResult:
    """一次外部减损动作的执行结果（可审计，不含错误栈正文）。"""

    kind: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None
    trail: tuple[str, ...] = field(default_factory=tuple)

    @property
    def applied(self) -> bool:
        return self.status == STATUS_APPLIED

    @property
    def failed(self) -> bool:
        return self.status == STATUS_FAILED


def _brief(text: str) -> str:
    return " ".join(text.split())[:_ERROR_SUMMARY_LIMIT]


async def apply_with_retry(
    kind: str,
    action: Callable[[], Awaitable[None]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ExternalMitigationResult:
    """执行外部减损动作并独立重试（**不抛异常**，失败以状态返回）。

    Args:
        kind: 动作种类（如 `STOP_ENGINE_JOB`）。
        action: 实际调用（无参协程函数）。
        max_attempts: 重试预算（默认 3；非正数按 1 处理，至少尝试一次）。

    Returns:
        ExternalMitigationResult: 终态结果（APPLIED / FAILED）与尝试轨迹。
    """
    budget = max(int(max_attempts), 1)
    trail: list[str] = []
    last_error: str | None = None
    for attempt in range(1, budget + 1):
        trail.append(f"{STATUS_PENDING}#{attempt}")
        trail.append(f"{STATUS_APPLYING}#{attempt}")
        try:
            await action()
        except Exception as exc:  # noqa: BLE001 -- 外部动作失败必须被吞掉（禁止事项 18）
            last_error = f"{type(exc).__name__}: {_brief(str(exc))}"
            trail.append(f"{STATUS_FAILED}#{attempt}")
            continue
        trail.append(f"{STATUS_APPLIED}#{attempt}")
        return ExternalMitigationResult(
            kind=kind,
            status=STATUS_APPLIED,
            attempts=attempt,
            max_attempts=budget,
            trail=tuple(trail),
        )
    return ExternalMitigationResult(
        kind=kind,
        status=STATUS_FAILED,
        attempts=budget,
        max_attempts=budget,
        last_error=last_error,
        trail=tuple(trail),
    )


def not_required(kind: str) -> ExternalMitigationResult:
    """无外部动作时的结果（NOT_REQUIRED 终态）。"""
    return ExternalMitigationResult(
        kind=kind,
        status=STATUS_NOT_REQUIRED,
        attempts=0,
        max_attempts=0,
        trail=(STATUS_NOT_REQUIRED,),
    )


async def record_external_mitigation(
    session: AsyncSession, run_id: uuid.UUID, result: ExternalMitigationResult
) -> None:
    """把外部减损动作结果写入事件流（独立于快照，失败也不影响快照写入）。"""
    level = "INFO" if result.status in {STATUS_APPLIED, STATUS_NOT_REQUIRED} else "ERROR"
    detail = (
        f"外部减损动作 {result.kind} → {result.status}"
        f"（尝试 {result.attempts}/{result.max_attempts}）"
    )
    if result.last_error:
        detail = f"{detail}；最后错误：{result.last_error}"
    await record_event(
        session,
        run_id,
        level=level,
        event_type="execution.external_mitigation",
        message=detail,
        stage="supervise",
    )


__all__ = [
    "ALL_STATUSES",
    "DEFAULT_MAX_ATTEMPTS",
    "STATUS_APPLIED",
    "STATUS_APPLYING",
    "STATUS_FAILED",
    "STATUS_NOT_REQUIRED",
    "STATUS_PENDING",
    "TERMINAL_STATUSES",
    "ExternalMitigationResult",
    "apply_with_retry",
    "not_required",
    "record_external_mitigation",
]
