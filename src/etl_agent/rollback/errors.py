"""M-L 受管回滚 — 回滚域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-L / 第 6.1 章 API-13
覆盖 FR：FR-48（回滚申请 / 审批 / 执行状态机、保留期与清理）
对应 AC：AC-08（受管回滚经审批后恢复原状态）

口径（S8 提示词 §8.14）：新增 2 个回滚域码；其余场景复用 SPEC 6.4 与既有 AMEND：

| 场景 | 码 | 来源 |
|------|----|------|
| 回滚申请 / 制品不存在或跨项目 | `BIZ_404_ROLLBACK_NOT_FOUND` | S8 新增（→ AMEND-007） |
| 回滚状态机不允许当前操作（未审批即执行 / 终态重复操作） | `BIZ_409_ROLLBACK_STATE_CONFLICT` | S8 新增（→ AMEND-007） |
| 运行状态不允许回滚（QUEUED/RUNNING 等） | `BIZ_409_STATE_CONFLICT` | SPEC 6.4（API-13 契约明列） |
| PDP DENY（跨项目 / Secret 外发类） | `HARNESS_403_POLICY_DENY` | SPEC 6.4（API-13 契约明列） |
| 审批不足 / 自批 / 槽被占 | `HARNESS_409_APPROVAL_INSUFFICIENT` / `_SELF_APPROVAL` / `_ROLE_TAKEN` | SPEC 6.4（S6 复用） |
| 运行不存在或跨项目 | `BIZ_404_EXECUTION_NOT_FOUND` | AMEND-006 |
| 制品元数据无效（缺字段 / 摘要不合法） | `BIZ_409_ROLLBACK_STATE_CONFLICT` | 复用（处置与状态冲突一致：修元数据后重试） |

错误摘要一律不含凭据与错误栈正文（AC-02 / S8 禁止事项 16）。
"""

from __future__ import annotations

from etl_agent.auth.errors import BizError


class RollbackNotFound(BizError):
    """回滚申请 / 回滚制品不存在或跨项目（不泄露存在性）。"""

    code = "BIZ_404_ROLLBACK_NOT_FOUND"
    http_status = 404


class RollbackStateConflict(BizError):
    """回滚状态机不允许当前操作（含制品元数据无效）。"""

    code = "BIZ_409_ROLLBACK_STATE_CONFLICT"
    http_status = 409


__all__ = ["RollbackNotFound", "RollbackStateConflict"]
