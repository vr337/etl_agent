"""M-C 凭据治理 — 凭据域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 8 章「安全 · 凭据保护」行
覆盖 FR：FR-06（SecretProvider 双模式）、FR-07/08/09（轮换/迁移/销毁状态机）
对应 AC：AC-02（全链路无敏感明文泄露）

连接域错误码（BIZ_404_CONNECTION_NOT_FOUND 等）见 connections/errors.py。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class SecretError(BizError):
    """凭据治理错误基类。"""


class SecretNotFound(SecretError):
    """Secret 引用不存在（数据完整性错误）。"""

    code = "BIZ_404_NOT_FOUND"
    http_status = 404


class SecretDestroyed(SecretError):
    """Secret 已销毁（墓碑）：拒绝读取（FR-06：销毁后保留不含秘密的墓碑）。"""

    code = "BIZ_409_STATE_CONFLICT"
    http_status = 409


class SecretStateConflict(SecretError):
    """凭据治理状态机不允许的转换（FR-07/08：状态不可跳跃）。"""

    code = "BIZ_409_STATE_CONFLICT"
    http_status = 409


class VaultUnavailable(SecretError):
    """Vault 模式不可用（Q5/S-Q7 待定：未部署时明确失败，不静默降级到 fernet）。"""

    code = "SYS_503_DEPENDENCY_UNAVAILABLE"
    http_status = 503
