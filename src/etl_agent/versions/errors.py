"""M-H 不可变版本与设计审批 — 领域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-H / 第 6.1 章 API-07
覆盖 FR：FR-26（状态机不允许 / 乐观锁冲突）、FR-28（审批状态冲突）
对应 AC：AC-04（不可变版本冻结）

口径（S6 人类裁决）：版本域的「状态冲突 / 乐观锁冲突 / 未冻结」**复用 SPEC 6.4 已有的**
`BIZ_409_STATE_CONFLICT`，不新增专用错误码——三者对调用方的处置完全相同（重新读取版本后重试），
单独设码只会增加契约面。故本文件不产生 AMEND-005 条目。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class VersionNotFound(BizError):
    """版本不存在或跨项目（复用 SPEC 6.4 `BIZ_404_NOT_FOUND`，不泄露存在性）。"""

    code = "BIZ_404_NOT_FOUND"
    http_status = 404


class VersionStateConflict(BizError):
    """版本状态机不允许该迁移（含乐观锁 `lock_version` 冲突，复用 SPEC 6.4 `BIZ_409_STATE_CONFLICT`）。"""

    code = "BIZ_409_STATE_CONFLICT"
    http_status = 409


class ArtifactNotFrozen(BizError):
    """版本尚未冻结制品（无 `artifact_sha256`），不可提交设计审批（复用 `BIZ_409_STATE_CONFLICT`）。"""

    code = "BIZ_409_STATE_CONFLICT"
    http_status = 409


class ApprovalNotFound(BizError):
    """设计审批申请不存在（复用 `BIZ_404_NOT_FOUND`）。"""

    code = "BIZ_404_NOT_FOUND"
    http_status = 404


class ApprovalSelfForbidden(BizError):
    """设计审批申请人自批（PRD 2.3-1 职责分离红线，复用 `HARNESS_409_SELF_APPROVAL`）。"""

    code = "HARNESS_409_SELF_APPROVAL"
    http_status = 409


class ApprovalAlreadyDecided(BizError):
    """设计审批申请已有决策（复用 `BIZ_409_STATE_CONFLICT`）。"""

    code = "BIZ_409_STATE_CONFLICT"
    http_status = 409
