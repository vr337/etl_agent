"""控制面入口：FastAPI 应用装配（认证中间件 + 项目治理路由）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A/M-B / 第 4.3 章进程与入口 / 第 6.4 章错误码
覆盖 FR：FR-59（/health）、FR-65（认证中间件挂载）、FR-01/02/03（项目治理路由）
对应 AC：AC-11（生产启动强制校验）、AC-12（多租户隔离）
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from etl_agent.auth.errors import AppError
from etl_agent.auth.middleware import AuthMiddleware
from etl_agent.config import Settings
from etl_agent.config import settings as default_settings
from etl_agent.infrastructure.database import engine
from etl_agent.infrastructure.redis_client import close_redis_client, create_redis_client
from etl_agent.projects.router import router as projects_router


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动：生产强制校验（AC-11）+ Bootstrap 配置校验 + Redis 客户端；关闭：优雅释放连接池。"""
    settings: Settings = app.state.settings
    # 生产缺失配置即拒绝启动，不降级放行（AC-11 / SPEC 4.4-3）
    settings.validate_production()
    settings.validate_bootstrap()
    app.state.redis = create_redis_client(settings.resolved_redis_url)
    yield
    await close_redis_client(app.state.redis)
    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """装配应用（测试可注入自定义 Settings，如 trusted-gateway 密钥 / Bootstrap 开关）。

    Args:
        settings: 应用配置；缺省用 config.settings（infra/.env）。

    Returns:
        FastAPI: 挂载认证中间件与路由的应用实例。
    """
    app = FastAPI(title="etl_agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or default_settings
    app.add_middleware(AuthMiddleware, settings=app.state.settings)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """存活状态（FR-59：S2 不主动读取凭据，仅返回存活）。"""
        return {"status": "ok"}

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """统一错误响应 {code, message, trace_id}（SPEC 6.4；trace_id 与请求贯通）。"""
        trace_id = getattr(request.state, "trace_id", None) or "unknown"
        return JSONResponse(status_code=exc.http_status, content=exc.to_content(trace_id))

    app.include_router(projects_router)
    return app


app = create_app()


def main() -> None:
    """uvicorn 入口（pyproject scripts: etl-agent-api）。"""
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
