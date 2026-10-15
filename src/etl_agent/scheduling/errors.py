"""M-K/T-14 调度 — 调度域错误体系（复用 SPEC 6.4 既有码，不新增）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-K / 第 6.1 章 API-11/12/14
覆盖 FR：FR-40（cron + 时区 + misfire 策略参数校验）、FR-41（水印增量契约字段校验）
对应 AC：AC-06（调度参数非法时失败关闭，不静默降级为「永不触发」）

口径：调度域**不新增错误码**（S7 提示词 §8.12 只列执行/引擎 6 个新码）。三类场景
在 SPEC 6.4 已有等价码，语义一致即复用：

| 场景 | 复用码 | 理由 |
|------|--------|------|
| 调度策略不存在 / 跨项目 | `BIZ_404_NOT_FOUND` | 与其余域一致，不泄露存在性 |
| 调度策略与管线不匹配等状态冲突 | `BIZ_409_STATE_CONFLICT` | 状态机不允许 |
| cron / 时区 / 模式 / misfire 参数非法 | `BIZ_400_VALIDATION` | 参数校验失败 |

错误摘要一律不含凭据（AC-02）。
"""
from __future__ import annotations

from etl_agent.auth.errors import NotFound, StateConflict, ValidationError


class ScheduleNotFound(NotFound):
    """调度策略不存在或跨项目（复用 SPEC 6.4 `BIZ_404_NOT_FOUND`）。"""


class ScheduleStateConflict(StateConflict):
    """调度策略与管线 / 项目不匹配（复用 SPEC 6.4 `BIZ_409_STATE_CONFLICT`）。"""


class ScheduleValidationError(ValidationError):
    """cron 表达式、时区、同步模式或 misfire 参数非法（复用 `BIZ_400_VALIDATION`）。"""


__all__ = ["ScheduleNotFound", "ScheduleStateConflict", "ScheduleValidationError"]
