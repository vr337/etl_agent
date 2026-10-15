"""M-N 审计证据 — 审计域错误体系（复用 SPEC 6.4 既有码，不新增）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-N
覆盖 FR：FR-36（审计事件记录）
对应 AC：AC-02（错误摘要零明文）

口径：审计域**不新增错误码**——审计是写路径的横切关注点，其失败在 SPEC 6.4 已有等价码：

| 场景 | 复用码 | 理由 |
|------|--------|------|
| 审计事件写入失败（外部依赖异常） | `SYS_500_INTERNAL` | 内部错误，且 `record_safely` 已降级为日志 |
| 项目不存在 / 非成员 | `AUTH_403_NOT_PROJECT_MEMBER` | SPEC 6.4（S2 已交付） |

审计失败**不抛业务异常**（`service.record_safely` 兜底）——审计问题的正确处置是留下
可见告警，而不是把「审计不可用」升级成「业务不可用」（S9 提示词 §8.2）。
"""
from __future__ import annotations

from etl_agent.auth.errors import AppError


class AuditWriteFailure(AppError):
    """审计写入失败（复用 SPEC 6.4 `SYS_500_INTERNAL`；仅用于显式调用路径）。"""

    code = "SYS_500_INTERNAL"
    http_status = 500


__all__ = ["AuditWriteFailure"]
