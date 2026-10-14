"""M-A 认证鉴权 — dev-header 开发身份头模式。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 4.2 章环境差异（dev 行）
覆盖 FR：FR-58（统一经 X-Actor-ID 头注入操作者身份；dev-header 仅限非生产）
对应 AC：AC-11（生产环境启用 dev-header → 启动拒绝，见 config.validate_production）
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from etl_agent.auth.errors import UnknownActor

HEADER_ACTOR_ID = "X-Actor-ID"

# 认证器签名：请求头映射 → actor subject（trusted_gateway 侧同构，便于中间件按模式互换）
Authenticator = Callable[[Mapping[str, str]], Awaitable[str]]


def extract_actor_subject(headers: Mapping[str, str]) -> str:
    """从请求头提取 X-Actor-ID（FR-58）。

    Args:
        headers: 请求头映射（Starlette Headers，大小写不敏感）。

    Returns:
        str: actor subject（uuid 或登录主体串）。

    Raises:
        UnknownActor: Header 缺失或为空——没有身份声明即视为身份未知（401）。
    """
    subject = headers.get(HEADER_ACTOR_ID)
    if not subject or not subject.strip():
        raise UnknownActor(f"缺少 {HEADER_ACTOR_ID} 头")
    return subject.strip()


def make_dev_header_authenticator() -> Authenticator:
    """构造 dev-header 认证器（中间件按 AUTH_MODE 选用）。

    dev-header 不做任何签名校验——它是开发期便当模式；生产可用性由
    config.validate_production 在启动期阻断（FR-58：生产由 trusted-gateway 签名替代）。

    Returns:
        异步认证函数：headers → actor subject。
    """

    async def authenticate(headers: Mapping[str, str]) -> str:
        return extract_actor_subject(headers)

    return authenticate
