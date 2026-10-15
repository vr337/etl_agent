"""trace_id 贯通用例（T-20.4 / FR-60）。

覆盖：错误响应附 trace_id（AppError 与未处理异常两条路径）、响应头回写
`X-Trace-Id`、入站 trace_id 透传与非法值忽略、trace_id 与日志同源。
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.observability.errors import TRACE_ID_HEADER, resolve_trace_id
from tests.helpers import SeededWorkspace, make_client

VALID_TRACE_ID = "0123456789abcdef0123456789abcdef"


async def test_app_error_response_has_trace_id(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """AppError（404）响应体含 trace_id，且与响应头一致（可据此检索日志）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/executions/{uuid.uuid4()}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "BIZ_404_EXECUTION_NOT_FOUND"
    assert body["trace_id"]
    assert response.headers[TRACE_ID_HEADER] == body["trace_id"]


async def test_incoming_trace_id_is_propagated(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无活动 span（OTel 未启用 / 无链路）时透传入站 `X-Trace-Id`。

    显式把「当前活动 span」置空：OTel 的 context 是进程级可传播的，
    若前置用例留下过活动 span，本用例就会变成在验证另一条优先级分支。
    """
    from etl_agent.observability import errors as obs_errors

    monkeypatch.setattr(obs_errors, "current_trace_id", lambda: None)
    async with make_client(app) as client:
        response = await client.get("/health", headers={TRACE_ID_HEADER: VALID_TRACE_ID})

    assert response.status_code == 200
    assert response.headers[TRACE_ID_HEADER] == VALID_TRACE_ID


async def test_invalid_incoming_trace_id_is_ignored(app: FastAPI) -> None:
    """非法入站 trace_id 不原样回显（防任意字符串注入日志）。"""
    async with make_client(app) as client:
        response = await client.get(
            "/health", headers={TRACE_ID_HEADER: "not-a-trace-id<script>"}
        )

    assert response.status_code == 200
    generated = response.headers[TRACE_ID_HEADER]
    assert generated != "not-a-trace-id<script>"
    assert len(generated) == 32


async def test_unhandled_exception_returns_500_with_trace_id(
    db_session: AsyncSession,
) -> None:
    """未处理异常 → 500 `SYS_500_INTERNAL` + trace_id；异常栈只进日志（AC-02）。

    注意：未处理异常由 Starlette 最外层的 `ServerErrorMiddleware` 生成响应，
    它位于可观测中间件之外，故响应头**不带** `X-Trace-Id`；trace_id 由错误体承载
    （这是框架分层决定的，不是遗漏——正常路径的响应头仍恒有该字段）。
    """
    from tests.helpers import build_test_app

    application = await build_test_app(db_session)

    @application.get("/boom")
    async def boom() -> None:  # pragma: no cover - 路由体只用于触发异常
        raise RuntimeError("kaboom-internal-detail")

    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "SYS_500_INTERNAL"
    assert len(body["trace_id"]) == 32
    assert "kaboom-internal-detail" not in response.text  # 内部细节不外泄


async def test_trace_id_present_on_every_response(app: FastAPI) -> None:
    """成功响应也回写 trace_id（成功链路同样要能关联日志）。"""
    async with make_client(app) as client:
        response = await client.get("/health")
    assert response.headers.get(TRACE_ID_HEADER)


def test_resolve_trace_id_generates_when_absent() -> None:
    """无 span、无入站头时生成 32 位 hex（保证任何请求都有可检索 id）。"""

    class _Request:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.state = type("_State", (), {})()

    resolved = resolve_trace_id(_Request())  # type: ignore[arg-type]
    assert len(resolved) == 32
    assert all(char in "0123456789abcdef" for char in resolved)
