"""M-A 认证鉴权 — 认证 / 业务错误码与异常体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系（段位划分）
覆盖 FR：FR-65（认证鉴权）、FR-66（多租户隔离）
对应 AC：AC-11（认证边界）、AC-12（多租户隔离）
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """应用错误基类：统一 JSON 响应 {code, message, trace_id}（SPEC 6.4）。"""

    code: str = "SYS_500_INTERNAL"
    http_status: int = 500
    message: str = "内部错误"

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)

    def to_content(self, trace_id: str) -> dict[str, Any]:
        """错误响应体（PRD 10.5：绝不包含凭证明文）。"""
        return {"code": self.code, "message": self.message, "trace_id": trace_id}


# ============================================================
# AUTH_ 段（SPEC 6.4）
# ============================================================


class AuthError(AppError):
    """认证 / 授权错误基类。"""


class UnknownActor(AuthError):
    """身份未知：subject 在 users 不存在（SPEC 6.4 AUTH_401_UNKNOWN_ACTOR）。"""

    code = "AUTH_401_UNKNOWN_ACTOR"
    http_status = 401


class BadSignature(AuthError):
    """签名无效 / Header 缺失 / Nonce 重放（SPEC 6.4 AUTH_401_BAD_SIGNATURE）。

    重放（已消费 Nonce）并入本码而非单独设码：对外不区分「签名错」与「重放」，
    避免向攻击者反馈 Nonce 状态（SPEC 8「安全 · 防重放与授权」行）。
    """

    code = "AUTH_401_BAD_SIGNATURE"
    http_status = 401


class TimestampSkew(AuthError):
    """时钟偏移超限（>60s，SPEC 6.4 AUTH_401_TIMESTAMP_SKEW / FR-65）。"""

    code = "AUTH_401_TIMESTAMP_SKEW"
    http_status = 401


class NotProjectMember(AuthError):
    """非项目成员（SPEC 6.4 AUTH_403_NOT_PROJECT_MEMBER / FR-66）。

    项目不存在与无权限统一返回本错误，不泄露项目是否存在（AC-12）。
    """

    code = "AUTH_403_NOT_PROJECT_MEMBER"
    http_status = 403


class RoleRequired(AuthError):
    """缺角色：项目管理员或 Harness 职责（SPEC 6.4 AUTH_403_ROLE_REQUIRED）。"""

    code = "AUTH_403_ROLE_REQUIRED"
    http_status = 403


class NonceStoreUnavailable(AuthError):
    """Nonce 存储不可用：失败关闭返回 503，不降级放行（SPEC 6.4 / FR-65）。"""

    code = "AUTH_503_NONCE_STORE_UNAVAILABLE"
    http_status = 503


# ============================================================
# BIZ_ 段（SPEC 6.4；S2 复用 + API-01 补全）
# ============================================================


class BizError(AppError):
    """业务错误基类。"""


class ValidationError(BizError):
    """参数校验失败（SPEC 6.4 BIZ_400_VALIDATION）。"""

    code = "BIZ_400_VALIDATION"
    http_status = 400


class NotFound(BizError):
    """资源不存在（SPEC 6.4 BIZ_404_NOT_FOUND）。"""

    code = "BIZ_404_NOT_FOUND"
    http_status = 404


class StateConflict(BizError):
    """状态机不允许（SPEC 6.4 BIZ_409_STATE_CONFLICT），如 Bootstrap 二次调用。"""

    code = "BIZ_409_STATE_CONFLICT"
    http_status = 409


class UniqueConstraintViolation(BizError):
    """唯一约束冲突（SPEC 6.4 BIZ_409_UNIQUE_CONSTRAINT），如 (project_id, user_id)。"""

    code = "BIZ_409_UNIQUE_CONSTRAINT"
    http_status = 409


class BootstrapDisabled(BizError):
    """Bootstrap 未启用（S2 新增：SPEC 6.2 未定义 API-01 错误码，按 S2 提示词 9.5 的 403 语义补全）。"""

    code = "BIZ_403_BOOTSTRAP_DISABLED"
    http_status = 403


class BootstrapTokenInvalid(BizError):
    """Bootstrap Token 不匹配（S2 新增：SPEC 6.2 未定义 API-01 错误码，按 S2 提示词 9.5 的 403 语义补全）。"""

    code = "BIZ_403_BOOTSTRAP_TOKEN_INVALID"
    http_status = 403
