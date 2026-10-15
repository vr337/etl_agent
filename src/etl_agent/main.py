"""控制面入口：FastAPI 应用装配（认证中间件 + 项目治理 / 连接 / 探查 / 文件资产 / 生成 / 版本 / Harness / 执行 / 调度路由）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A~M-O / 第 4.3 章进程与入口 / 第 6.4 章错误码 / 第 8 章「可观测」
覆盖 FR：FR-59/60（/health 与链路可观测）、FR-65（认证中间件挂载）、FR-01/02/03（项目治理路由）、
FR-04/05/11（连接路由）、FR-12/14/15（探查路由）、FR-13（文件资产路由）、FR-16/17/26（生成与版本路由）、
FR-29~35（Harness 内核路由）、FR-38/39（执行路由 API-11/12/14）、FR-40/41（调度路由）
对应 AC：AC-11（生产启动强制校验）、AC-12（多租户隔离）、AC-02（凭据零明文）、AC-03（中断与恢复）、
AC-05（Harness 三阶段协议）、AC-06（执行与调度可观测）
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import AppError
from etl_agent.auth.middleware import AuthMiddleware
from etl_agent.benchmark.router import router as benchmark_router
from etl_agent.config import Settings
from etl_agent.config import settings as default_settings
from etl_agent.connections.router import router as connections_router
from etl_agent.evidence.router import router as evidence_router
from etl_agent.execution.router import router as execution_router
from etl_agent.files.router import router as file_assets_router
from etl_agent.generation.checkpoint import open_checkpointer
from etl_agent.generation.router import router as generation_router
from etl_agent.harness.commit.service import CommitService
from etl_agent.harness.pdp.engine import PdpEngine
from etl_agent.harness.policy.package import PolicyPackageInvalid, load_package
from etl_agent.harness.prepare.service import PrepareService
from etl_agent.harness.router import router as harness_router
from etl_agent.infrastructure.database import engine, get_session
from etl_agent.infrastructure.redis_client import close_redis_client, create_redis_client
from etl_agent.llm.factory import create_provider
from etl_agent.observability.errors import install_trace_id
from etl_agent.observability.health import health_payload
from etl_agent.observability.logging import configure_logging
from etl_agent.observability.otel import setup_otel, shutdown_otel
from etl_agent.profiles.router import router as profiles_router
from etl_agent.projects.router import router as projects_router
from etl_agent.rollback.router import router as rollback_router
from etl_agent.safety.router import router as safety_router
from etl_agent.scheduling.router import router as scheduling_router
from etl_agent.versions.router import router as versions_router
from etl_agent.worker.capability_handoff import RedisCapabilityHandoff

logger = logging.getLogger("etl_agent.main")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动：生产强制校验（AC-11）+ Bootstrap/SecretProvider/LLM/Harness/Worker 配置校验 + Redis + Checkpointer + OTel。

    Harness 侧额外做**策略包加载与验签**（FR-30）：缺失 / 篡改 / 签名不过即拒绝启动，
    不降级放行——没有可信策略的 PDP 等于没有门禁。

    关闭：先释放 Checkpointer（etl_checkpoint 库连接），再冲刷 OTel、释放 Redis 与业务库连接池。
    """
    settings: Settings = app.state.settings
    configure_logging()
    # 生产缺失配置即拒绝启动，不降级放行（AC-11 / SPEC 4.4-3）
    settings.validate_production()
    settings.validate_bootstrap()
    # SecretProvider 配置校验（FR-06：fernet key / vault 地址缺失即拒绝启动）
    settings.validate_secrets()
    # LLM Provider 校验（FR-18/21：provider 白名单、预算 1–3、生产禁 mock）
    settings.validate_llm()
    # Harness 内核校验（FR-29/30/31/33：策略包路径、TTL 边界、生产密钥）
    settings.validate_harness()
    # 执行调度校验（FR-38/40：周期为正、SeaTunnel 地址必填、OTel 启用即需端点）
    settings.validate_worker()
    # 审计证据与评测校验（S9：FR-36 检查点间隔、FR-50 P95 基准）
    settings.validate_evidence()

    app.state.redis = create_redis_client(settings.resolved_redis_url)
    # Capability 交接通道（FR-33：令牌本体只经 Redis 单次读到达 Worker，不落业务库）
    app.state.capability_handoff = RedisCapabilityHandoff(
        app.state.redis, key_prefix=settings.capability_handoff_key_prefix
    )
    app.state.llm_provider = create_provider(settings)
    # OTel 装配（FR-60）：关闭路径零成本，不建 Provider / 不注册埋点
    setup_otel(app, settings)
    try:
        package = load_package(
            settings.policy_package_path,
            settings.policy_package_signature_path,
            settings.harness_public_key_list,
        )
    except PolicyPackageInvalid as exc:
        raise PolicyPackageInvalid(
            f"Harness 策略包不可用，拒绝启动（FR-30）：{exc}"
        ) from exc
    app.state.pdp_package = package
    app.state.prepare_service = PrepareService(
        PdpEngine(package), requested_ttl_seconds=settings.harness_preparation_ttl_seconds
    )
    from etl_agent.harness.capability.issuer import CapabilityIssuer

    app.state.commit_service = CommitService(
        CapabilityIssuer(settings.harness_signing_key_hex),
        ttl_seconds=settings.harness_capability_ttl_seconds,
    )
    # 双库分离（ADR-03）：Checkpointer 只连 etl_checkpoint，物理上写不到业务库
    async with open_checkpointer(settings) as checkpointer:
        app.state.checkpointer = checkpointer
        yield
    await close_redis_client(app.state.redis)
    shutdown_otel()
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
    # trace_id 中间件与全局异常处理器（FR-60）：错误响应与日志同源
    install_trace_id(app)

    @app.get("/health")
    async def health(
        request: Request, session: AsyncSession = Depends(get_session)
    ) -> dict[str, Any]:
        """存活 + 脱敏配置状态 + Worker 摘要（FR-59/38）。

        探针语义：**库不可用也必须回存活**——存活探针失败会导致编排器重启容器，
        而「库挂了」不该由重启 API 来修。故库查询失败时降级为不含 Worker 摘要。
        本端点不主动读取任何凭据（FR-59）。
        """
        effective: Settings = request.app.state.settings
        try:
            return await health_payload(session, effective)
        except Exception:
            logger.debug("Worker 摘要查询失败，降级为无摘要的存活响应", exc_info=True)
            return await health_payload(None, effective)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """统一错误响应 {code, message, trace_id}（SPEC 6.4；trace_id 与请求贯通）。"""
        trace_id = getattr(request.state, "trace_id", None) or "unknown"
        return JSONResponse(status_code=exc.http_status, content=exc.to_content(trace_id))

    app.include_router(projects_router)
    app.include_router(connections_router)
    app.include_router(profiles_router)
    app.include_router(file_assets_router)
    app.include_router(generation_router)
    app.include_router(versions_router)
    app.include_router(harness_router)
    app.include_router(execution_router)
    app.include_router(scheduling_router)
    app.include_router(rollback_router)
    # S9：审计证据（M-N）与评测安全进化（M-M）
    app.include_router(evidence_router)
    app.include_router(benchmark_router)
    app.include_router(safety_router)
    return app


app = create_app()


def main() -> None:
    """uvicorn 入口（pyproject scripts: etl-agent-api）。"""
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
