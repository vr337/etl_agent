"""M-O 可观测性 — 错误响应附 trace_id（全局异常处理器 + 链路透传，FR-60）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-O / 第 6.4 章错误码体系（`{code, message, trace_id}`）
覆盖 FR：FR-60（错误响应附带 trace_id）、FR-59（`/health` 之外的健康信号不泄露内部细节）
对应 AC：AC-02（错误响应不含凭据与内部栈）、AC-11（链路可追）

trace_id 的来源优先级（**必须与日志同源**，否则「日志搜得到、响应对不上」）：

1. 当前活动 OTel span 的 trace_id（OTel 启用时的权威值，日志用的就是它）；
2. 入站 `X-Trace-Id` 头（网关 / 上游已生成时透传，OTel 关闭时仍可关联）；
3. 自行生成的 32 位 hex（兜底：无任何上游链路信息时也必须给出可检索的 id）。

响应头回写 `X-Trace-Id`：调用方可以据此把「用户报障的这一次请求」与日志对齐。
未处理异常统一落 `SYS_500_INTERNAL`（SPEC 6.4）：响应只回 code/message/trace_id，
异常栈只进日志不进响应体（AC-02）。
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from etl_agent.observability.logging import current_trace_id

TRACE_ID_HEADER = "X-Trace-Id"

# 内部错误码（SPEC 6.4 `SYS_500_INTERNAL`）
INTERNAL_ERROR_CODE = "SYS_500_INTERNAL"
INTERNAL_ERROR_MESSAGE = "内部错误"

logger = logging.getLogger("etl_agent.observability.errors")

# trace_id 形态约束（32 位 hex；外部传入不合法即忽略，避免注入任意字符串进日志）
TRACE_ID_LENGTH = 32


def resolve_trace_id(request: Request) -> str:
    """解析本次请求的 trace_id（活动 span → 入站头 → 新生成）。"""
    active = current_trace_id()
    if active:
        return active
    incoming = request.headers.get(TRACE_ID_HEADER, "").strip().lower()
    if _is_valid_trace_id(incoming):
        return incoming
    return uuid.uuid4().hex


def _is_valid_trace_id(value: str) -> bool:
    return len(value) == TRACE_ID_LENGTH and all(
        char in "0123456789abcdef" for char in value
    )


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求确定 trace_id：写入 `request.state` 并回写响应头。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        trace_id = resolve_trace_id(request)
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers[TRACE_ID_HEADER] = trace_id
        return response


def install_trace_id(app: FastAPI) -> None:
    """装配 trace_id 中间件与未处理异常的全局处理器（幂等：重复调用不重复注册）。"""
    if getattr(app.state, "trace_id_installed", False):
        return
    app.add_middleware(TraceIdMiddleware)

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """未处理异常：响应只回 code/message/trace_id，栈只进日志（AC-02）。"""
        trace_id = getattr(request.state, "trace_id", None) or resolve_trace_id(request)
        logger.exception("未处理异常：%s（trace_id=%s）", type(exc).__name__, trace_id)
        return JSONResponse(
            status_code=500,
            content={
                "code": INTERNAL_ERROR_CODE,
                "message": INTERNAL_ERROR_MESSAGE,
                "trace_id": trace_id,
            },
        )

    app.state.trace_id_installed = True


__all__ = [
    "INTERNAL_ERROR_CODE",
    "INTERNAL_ERROR_MESSAGE",
    "TRACE_ID_HEADER",
    "TraceIdMiddleware",
    "install_trace_id",
    "resolve_trace_id",
]
