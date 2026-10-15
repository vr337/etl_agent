"""M-J/M-K 执行 Worker — Worker 侧失败分类与错误码出口。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-J/M-K /
第 5.2 章 execution_runs（failure_category / failure_stage / retryable）
覆盖 FR：FR-47（失败分类与可重试判定）、FR-38（Worker 执行失败可观测）、FR-42（重试语义）
对应 AC：AC-06（失败分类落到执行运行，供运行中心与重试决策使用）

**本模块不新增错误码**（禁止事项 7）：错误码本体在 `execution.errors`（S7 新增 6 个，
→ AMEND-006 草稿）与 `harness.errors`（S6）中定义，本模块只做两件事：

1. 统一出口：Worker 侧消费 `execution.errors` / `harness.errors` 的异常；
2. 失败分类映射：异常类型 → `(failure_category, failure_stage, retryable)`。

分类口径（SPEC 5.2 `execution_runs.failure_category` 的 S7 子集，FR-47 九类中本期落四类）：

| 类别 | 含义 | 可重试 |
|------|------|--------|
| `CONFIGURATION` | 配置 / 授权事实类失败（令牌、租约、物化、引擎校验） | 视具体异常 |
| `CONNECTIVITY` | 依赖不可达（Redis 交接通道、引擎网络） | 是 |
| `ENGINE` | 引擎侧作业失败（提交被拒 / 作业失败） | 是 |
| `UNKNOWN` | 未分类（兜底，默认按可重试处置，避免误判永久失败） | 是 |

**授权类失败（令牌无效 / 过期 / 重放）一律不可重试**：重试不会让一张不可信的令牌变可信，
继续重试只会放大审计噪音；正确处置是重新 prepare→approve→commit（FR-33 单次令牌）。
"""
from __future__ import annotations

from etl_agent.execution.errors import (
    CredentialMaterializationFailed,
    DependencyUnavailable,
    ExecutionNotFound,
    ExecutionStateConflict,
    SeatunnelCheckFailed,
    SeatunnelSubmitFailed,
    SeatunnelUnavailable,
)
from etl_agent.harness.errors import (
    CapabilityExpired,
    CapabilityInvalid,
    CapabilityReplayed,
    LeaseHeld,
)

CATEGORY_CONFIGURATION = "CONFIGURATION"
CATEGORY_CONNECTIVITY = "CONNECTIVITY"
CATEGORY_ENGINE = "ENGINE"
CATEGORY_UNKNOWN = "UNKNOWN"

STAGE_DISPATCH = "dispatch"
STAGE_CHECK = "check"
STAGE_SUBMIT = "submit"
STAGE_RUN = "run"
STAGE_FINALIZE = "finalize"

# 异常类型 → (failure_category, failure_stage, retryable)
FAILURE_CLASSIFICATION: dict[type[BaseException], tuple[str, str, bool]] = {
    CapabilityInvalid: (CATEGORY_CONFIGURATION, STAGE_DISPATCH, False),
    CapabilityExpired: (CATEGORY_CONFIGURATION, STAGE_DISPATCH, False),
    CapabilityReplayed: (CATEGORY_CONFIGURATION, STAGE_DISPATCH, False),
    CredentialMaterializationFailed: (CATEGORY_CONFIGURATION, STAGE_DISPATCH, False),
    LeaseHeld: (CATEGORY_CONFIGURATION, STAGE_DISPATCH, True),
    ExecutionStateConflict: (CATEGORY_CONFIGURATION, STAGE_DISPATCH, False),
    ExecutionNotFound: (CATEGORY_CONFIGURATION, STAGE_DISPATCH, False),
    DependencyUnavailable: (CATEGORY_CONNECTIVITY, STAGE_DISPATCH, True),
    SeatunnelCheckFailed: (CATEGORY_CONFIGURATION, STAGE_CHECK, False),
    SeatunnelSubmitFailed: (CATEGORY_ENGINE, STAGE_SUBMIT, True),
    SeatunnelUnavailable: (CATEGORY_CONNECTIVITY, STAGE_SUBMIT, True),
}

DEFAULT_CLASSIFICATION: tuple[str, str, bool] = (CATEGORY_UNKNOWN, STAGE_DISPATCH, True)


def classify_failure(exc: BaseException) -> tuple[str, str, bool]:
    """把异常映射为 `(failure_category, failure_stage, retryable)`（FR-47 子集）。

    先按精确类型查表，再按 MRO 回退（子类继承父类分类），最后落 `UNKNOWN` 兜底。
    """
    for klass in type(exc).__mro__:
        found = FAILURE_CLASSIFICATION.get(klass)
        if found is not None:
            return found
    return DEFAULT_CLASSIFICATION


__all__ = [
    "CATEGORY_CONFIGURATION",
    "CATEGORY_CONNECTIVITY",
    "CATEGORY_ENGINE",
    "CATEGORY_UNKNOWN",
    "DEFAULT_CLASSIFICATION",
    "FAILURE_CLASSIFICATION",
    "STAGE_CHECK",
    "STAGE_DISPATCH",
    "STAGE_FINALIZE",
    "STAGE_RUN",
    "STAGE_SUBMIT",
    "classify_failure",
]
