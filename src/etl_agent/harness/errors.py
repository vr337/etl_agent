"""M-J Harness 安全执行内核 — 领域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-J / 第 6.2 章 API-08/09/10
覆盖 FR：FR-29（PDP DENY 无放行通道）、FR-32（四眼职责分离）、FR-33（Commit 重验与单次令牌）、FR-34（Tool Broker 与防重放）、FR-31（TTL 过期）
对应 AC：AC-01（四眼职责分离拦截）、AC-05（prepare→approve→commit + 单次令牌 + 防重放）

错误码口径：SPEC 6.4 已定义的 8 个 HARNESS_/BIZ_ 码**一律复用**（不新增）；
仅 3 个 SPEC 6.4 未覆盖的场景新增，已生成 AMEND-005 草稿：
- `HARNESS_403_TOOL_NOT_REGISTERED`：调用不在注册清单内的副作用（FR-34「非 Agent 自报」）；
- `HARNESS_401_CAPABILITY_INVALID`：Capability 签名 / 令牌哈希 / claims 绑定校验失败；
- `HARNESS_409_LEASE_HELD`：执行租约已被占用（FR-37 防并发重入）。
错误摘要一律不含凭据与 Prompt（AC-02）。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError

# ============================================================
# 复用 SPEC 6.4（不新增）
# ============================================================


class PolicyDeny(BizError):
    """PDP DENY：无人工放行通道（SPEC 6.4 `HARNESS_403_POLICY_DENY` / FR-29）。"""

    code = "HARNESS_403_POLICY_DENY"
    http_status = 403


class SelfApproval(BizError):
    """申请人自批：`(preparation_id, decided_by)` 唯一（SPEC 6.4 `HARNESS_409_SELF_APPROVAL` / PRD 2.3-1）。"""

    code = "HARNESS_409_SELF_APPROVAL"
    http_status = 409


class RoleTaken(BizError):
    """职责槽被占：`(preparation_id, role)` 唯一（SPEC 6.4 `HARNESS_409_ROLE_TAKEN` / PRD 2.3-2）。"""

    code = "HARNESS_409_ROLE_TAKEN"
    http_status = 409


class AlreadyCommitted(BizError):
    """重复 Commit（SPEC 6.4 `HARNESS_409_ALREADY_COMMITTED` / FR-33）。"""

    code = "HARNESS_409_ALREADY_COMMITTED"
    http_status = 409


class ApprovalInsufficient(BizError):
    """审批票数 / 槽位不足（SPEC 6.4 `HARNESS_409_APPROVAL_INSUFFICIENT` / FR-33）。"""

    code = "HARNESS_409_APPROVAL_INSUFFICIENT"
    http_status = 409


class CapabilityReplayed(BizError):
    """令牌重放：同一 Capability 二次消费（SPEC 6.4 `HARNESS_409_CAPABILITY_REPLAYED` / FR-34）。"""

    code = "HARNESS_409_CAPABILITY_REPLAYED"
    http_status = 409


class CapabilityExpired(BizError):
    """Capability 过期（SPEC 6.4 `HARNESS_401_CAPABILITY_EXPIRED`，TTL ≤900s）。"""

    code = "HARNESS_401_CAPABILITY_EXPIRED"
    http_status = 401


class PreparationExpired(BizError):
    """准备单 TTL 过期（SPEC 6.4 `BIZ_410_PREPARATION_EXPIRED`，默认 24h）。"""

    code = "BIZ_410_PREPARATION_EXPIRED"
    http_status = 410


class IdempotencyConflict(BizError):
    """同幂等键不同载荷（SPEC 6.4 `BIZ_409_IDEMPOTENCY_CONFLICT` / FR-31）。"""

    code = "BIZ_409_IDEMPOTENCY_CONFLICT"
    http_status = 409


class PreparationNotFound(BizError):
    """准备单不存在或跨项目（复用 SPEC 6.4 `BIZ_404_NOT_FOUND`，不泄露存在性）。"""

    code = "BIZ_404_NOT_FOUND"
    http_status = 404


class PreparationStateConflict(BizError):
    """准备单状态机不允许该操作（复用 SPEC 6.4 `BIZ_409_STATE_CONFLICT`）。"""

    code = "BIZ_409_STATE_CONFLICT"
    http_status = 409


# ============================================================
# S6 新增（→ AMEND-005 草稿）
# ============================================================


class ToolNotRegistered(BizError):
    """副作用 / 工具不在注册清单内（FR-34：以注册清单判定，非 Agent 自报）。"""

    code = "HARNESS_403_TOOL_NOT_REGISTERED"
    http_status = 403


class CapabilityInvalid(BizError):
    """Capability 签名、令牌哈希或 claims 绑定校验失败（令牌不可信）。"""

    code = "HARNESS_401_CAPABILITY_INVALID"
    http_status = 401


class LeaseHeld(BizError):
    """执行租约已被占用：同一准备单的同类型副作用并发重入（FR-37）。"""

    code = "HARNESS_409_LEASE_HELD"
    http_status = 409
