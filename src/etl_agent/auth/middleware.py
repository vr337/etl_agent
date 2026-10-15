"""M-A 认证鉴权 — 认证中间件（按 AUTH_MODE 挂载）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 8 章「安全 · 认证」行
覆盖 FR：FR-58（身份头注入）、FR-65（trusted-gateway 验签 + Nonce 一次性）
对应 AC：AC-11（伪造签名 / 过期时间戳 / 已消费 Nonce → 401；Redis 不可用 → 503）

设计说明（T-03.9）：中间件只做「凭证校验」——验签 / Nonce 消费 / 身份头提取，
并把 actor_subject 注入 request.state；用户行与成员关系的解析在依赖层
（dependencies.require_project_member）完成，以复用请求级数据库事务
（中间件自建会话会与请求事务隔离，测试回滚将不可见）。
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from etl_agent.auth import trusted_gateway
from etl_agent.auth.dev_header import Authenticator, make_dev_header_authenticator
from etl_agent.auth.errors import AppError, BadSignature
from etl_agent.auth.nonce_store import verify_and_consume_nonce
from etl_agent.config import Settings

# 免鉴权路径：健康检查 / 首次初始化（自有 Token 校验）/ 文档
PUBLIC_PATHS = frozenset({"/health", "/api/v1/bootstrap", "/docs", "/redoc", "/openapi.json"})

# 仅 /api/v1/ 下的业务路径需要鉴权（其余路径交给路由层 404）
API_PREFIX = "/api/v1/"


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件：dev-header 或 trusted-gateway（Settings.auth_mode）。"""

    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings = settings
        self._dev_header_authenticator: Authenticator = make_dev_header_authenticator()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS or not path.startswith(API_PREFIX):
            return await call_next(request)

        # trace_id 沿用外层可观测中间件（S7 起由 TraceIdMiddleware 统一生成）已写入的值：
        # 此处再生成一个会让「响应头的 trace_id」与「错误体里的 trace_id」不一致——
        # 链路日志与用户报障就对不上了。未装可观测中间件时才自行兜底生成。
        trace_id = getattr(request.state, "trace_id", None) or uuid.uuid4().hex
        request.state.trace_id = trace_id
        try:
            subject = await self._authenticate(request)
        except AppError as exc:
            # 中间件内抛出的认证错误直接转响应（位于 ExceptionMiddleware 外层，拿不到统一处理器）
            return JSONResponse(status_code=exc.http_status, content=exc.to_content(trace_id))
        request.state.actor_subject = subject
        return await call_next(request)

    async def _authenticate(self, request: Request) -> str:
        if self._settings.auth_mode == "dev-header":
            return await self._dev_header_authenticator(request.headers)
        if self._settings.auth_mode == "trusted-gateway":
            return await self._verify_gateway(request)
        # 非法 AUTH_MODE 已在启动期被 config 校验拦截；运行期热改则失败关闭（AC-11）
        raise AppError("AUTH_MODE 非法，拒绝服务")

    async def _verify_gateway(self, request: Request) -> str:
        """trusted-gateway 验签（FR-65 / S2 提示词 9.2 校验顺序）。"""
        headers: Mapping[str, str] = request.headers
        actor = headers.get(trusted_gateway.HEADER_ACTOR, "")
        timestamp = headers.get(trusted_gateway.HEADER_TIMESTAMP, "")
        nonce = headers.get(trusted_gateway.HEADER_NONCE, "")
        signature = headers.get(trusted_gateway.HEADER_SIGNATURE, "")
        # 1. 四个 Header 缺失任一 → 401（不区分缺哪个，避免探测）
        if not (actor and timestamp and nonce and signature):
            raise BadSignature("缺少网关签名头")
        # 2. 时钟偏移 > 60s → 401 TIMESTAMP_SKEW
        trusted_gateway.verify_timestamp(timestamp)
        # 3. HMAC-SHA256 验签（规范载荷：actor/timestamp/nonce/METHOD/path?query）
        target = trusted_gateway.build_request_target(request.url.path, request.url.query)
        payload = trusted_gateway.build_canonical_payload(
            actor, timestamp, nonce, request.method, target
        )
        trusted_gateway.verify_signature(self._settings.gateway_hmac_secret, signature, payload)
        # 4. Nonce 长度 / 字符集 → 401
        trusted_gateway.validate_nonce_format(nonce)
        # 5. Nonce 一次性消费（SET NX）；Redis 不可用 → 503 失败关闭
        redis_client = request.app.state.redis
        await verify_and_consume_nonce(nonce, redis_client)
        return actor
