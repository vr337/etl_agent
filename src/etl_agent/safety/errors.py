"""M-M 评测与安全进化 — 安全进化域错误体系（提案 / 发布）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-M / 第 5.2 章 safety_*
覆盖 FR：FR-51（安全改进提案状态机）、FR-52（受控发布）
对应 AC：AC-09（安全进化受 Benchmark 门禁约束）

口径（S9 提示词 §8.10）：新增 4 个安全域码：

| 场景 | 码 | 来源 |
|------|----|------|
| 提案 / 发布不存在 | `BIZ_404_SAFETY_PROPOSAL_NOT_FOUND` | S9 新增（→ AMEND-008） |
| 状态机不允许当前操作（跳跃 / 终态重操作） | `BIZ_409_SAFETY_STATE_CONFLICT` | S9 新增（→ AMEND-008） |
| 小流量百分比越界（非 1%–10%） | `BIZ_422_SAFETY_CANARY_OUT_OF_RANGE` | S9 新增（→ AMEND-008） |
| 发布清单签名无效 / 缺失 | `BIZ_422_SAFETY_SIGNATURE_INVALID` | S9 新增（→ AMEND-008） |

复用：`AUTH_403_ROLE_REQUIRED`（仅 SECURITY_APPROVER 可裁决）。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class SafetyProposalNotFound(BizError):
    """安全提案 / 发布不存在（不泄露存在性）。"""

    code = "BIZ_404_SAFETY_PROPOSAL_NOT_FOUND"
    http_status = 404


class SafetyStateConflict(BizError):
    """状态机不允许当前操作（含状态跳跃 / 终态重复操作）。"""

    code = "BIZ_409_SAFETY_STATE_CONFLICT"
    http_status = 409


class SafetyCanaryOutOfRange(BizError):
    """小流量百分比越界（SPEC FR-52：必须落在 1%–10%）。"""

    code = "BIZ_422_SAFETY_CANARY_OUT_OF_RANGE"
    http_status = 422


class SafetySignatureInvalid(BizError):
    """发布清单 Ed25519 签名无效或缺失。"""

    code = "BIZ_422_SAFETY_SIGNATURE_INVALID"
    http_status = 422


__all__ = [
    "SafetyCanaryOutOfRange",
    "SafetyProposalNotFound",
    "SafetySignatureInvalid",
    "SafetyStateConflict",
]
