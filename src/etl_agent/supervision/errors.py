"""M-L 运行时监督与受管回滚 — 监督域错误体系（复用 SPEC 6.4 既有码，不新增）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-L / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（监督契约与预算执行）、FR-46（硬中断与隔离）
对应 AC：AC-08（监督失败可观测，且不因外部动作失败而丢失治理事实）

口径：S8 提示词 §8.14 的 6 个新码全部属于**发布门禁（execution 域）**与**回滚（rollback 域）**；
监督域本身**不新增错误码**——两类失败在 SPEC 6.4 已有等价码，语义一致即复用：

| 场景 | 复用码 | 理由 |
|------|--------|------|
| 外部减损动作依赖不可用（引擎 stop-job 不可达） | `SYS_503_DEPENDENCY_UNAVAILABLE` | 基础设施依赖失败，与 Redis 交接通道同类 |
| 监督动作与运行状态冲突（终态运行不可再减损） | `BIZ_409_STATE_CONFLICT` | 状态机不允许 |

错误摘要一律不含凭据与错误栈正文（AC-02 / S8 禁止事项 16）。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class MitigationUnavailable(BizError):
    """外部减损动作依赖不可用（复用 SPEC 6.4 `SYS_503_DEPENDENCY_UNAVAILABLE`）。

    注意：该失败**不阻塞监督快照写入**（S8 禁止事项 18）——快照是治理事实，
    外部动作只是执行手段，手段失败不能让事实消失。
    """

    code = "SYS_503_DEPENDENCY_UNAVAILABLE"
    http_status = 503


class SupervisionStateConflict(BizError):
    """监督动作与运行状态冲突（复用 SPEC 6.4 `BIZ_409_STATE_CONFLICT`）。"""

    code = "BIZ_409_STATE_CONFLICT"
    http_status = 409


__all__ = ["MitigationUnavailable", "SupervisionStateConflict"]
