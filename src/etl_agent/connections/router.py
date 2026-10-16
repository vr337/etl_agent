"""M-C 数据连接 — API 路由（API-02 登记 / API-03 测试 / 策略 / 详情）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-02/03 / 第 3.2 章 M-C
覆盖 FR：FR-04（连接管理）、FR-05（连通性测试）、FR-11（轮换策略）
对应 AC：AC-02（响应零明文）、AC-12（项目隔离）

凭据治理端点（轮换 / 迁移 / 销毁）在 secrets 域服务落地后由本路由器的
governance 分区挂载（S3 提示词 9.7~9.9 的路径均挂在 connections 之下）。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_admin, require_project_member
from etl_agent.config import Settings
from etl_agent.connections import policy as policy_service
from etl_agent.connections import probe as probe_service
from etl_agent.connections import register as register_service
from etl_agent.connections import service as connection_service
from etl_agent.connections.schemas import (
    ConnectionPolicyUpdateRequest,
    ConnectionRegisterRequest,
    ConnectionResponse,
    ConnectionTestResponse,
    DestructionCreateRequest,
    DestructionResponse,
    MigrationCreateRequest,
    MigrationResponse,
    RollbackResponse,
    RotationCreateRequest,
    RotationListView,
    RotationResponse,
)
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import DataConnection, SecretRotationRequest
from etl_agent.secrets import destruction as destruction_service
from etl_agent.secrets import migration as migration_service
from etl_agent.secrets import rotation as rotation_service
from etl_agent.secrets import rotation_rollback as rollback_service

router = APIRouter(prefix="/api/v1", tags=["connections"])


def _to_response(connection: DataConnection, username: str | None = None) -> ConnectionResponse:
    """连接响应：定位信息 + secret_ref；password/ciphertext 绝不出现在响应中（AC-02）。

    username 属连接定位信息（非敏感，S3 提示词 9.13），详情视图经内存中短暂解密提取；
    列表视图不解密（username=None）。
    """
    return ConnectionResponse(
        id=connection.id,
        name=connection.name,
        connector_type=connection.connector_type,
        status=connection.status,
        host=connection.host,
        port=connection.port,
        database_name=connection.database_name,
        username=username or "",
        secret_ref=connection.secret_ref,
        credential_max_age_days=connection.credential_max_age_days,
        credential_warning_days=connection.credential_warning_days,
        created_at=connection.created_at,
    )


@router.post("/projects/{project_id}/connections", status_code=201, response_model=ConnectionResponse)
async def register_connection(
    project_id: uuid.UUID,
    payload: ConnectionRegisterRequest,
    request: Request,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> ConnectionResponse:
    """登记数据连接（API-02，FR-04；写操作需 ADMIN）。"""
    connection = await register_service.register_connection(
        session, project_id=project_id, payload=payload, settings=request.app.state.settings
    )
    # username 直接回显请求值（无需解密）；password 已加密入库，绝不回显（AC-02）
    return _to_response(connection, username=payload.username)


@router.get("/projects/{project_id}/connections", response_model=list[ConnectionResponse])
async def list_connections(
    project_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[ConnectionResponse]:
    """列出项目连接（FR-04；列表不解密凭据）。"""
    connections = await connection_service.load_project_connections(session, project_id)
    return [_to_response(connection) for connection in connections]


@router.get(
    "/projects/{project_id}/connections/{connection_id}", response_model=ConnectionResponse
)
async def get_connection(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    request: Request,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> ConnectionResponse:
    """连接详情（FR-04；username 经短暂解密提取，password 仍不返回，S3 提示词 9.13）。"""
    connection = await connection_service.load_connection(session, project_id, connection_id)
    username: str | None = None
    if connection.secret_ref is not None:
        from etl_agent.secrets.errors import SecretError
        from etl_agent.secrets.factory import create_provider

        # 详情视图按 9.13 展示 username（定位信息）：内存中短暂持有，响应只取该字段
        try:
            provider = create_provider(session, _settings_of(request))
            secret = await provider.read(connection.secret_ref)
            username = secret.get("username")
        except SecretError:
            # 墓碑/损坏引用不阻断详情查看（username 留空，其余字段照常返回）
            username = None
    return _to_response(connection, username=username)


def _settings_of(request: Request) -> Settings:
    """从 app.state 取 Settings（测试可注入自定义配置）。"""
    from typing import cast

    return cast(Settings, request.app.state.settings)


@router.post(
    "/projects/{project_id}/connections/{connection_id}/test",
    response_model=ConnectionTestResponse,
)
async def test_connection(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> ConnectionTestResponse:
    """连通性测试（API-03，FR-05）：立即置 TESTING 返回，后台探针回写 READY/FAILED。"""
    connection = await connection_service.load_connection(session, project_id, connection_id)
    # 暂不支持类型在受理阶段明确失败（不进入后台、不产生静默失败，S3 禁止事项 15）
    probe_service.assert_probe_supported(connection)
    connection.status = "TESTING"
    await session.flush()
    started_at = probe_service.testing_started_at()
    # BackgroundTasks 在 FastAPI 依赖 ExitStack 内执行：请求会话仍然打开（fastapi 0.143 实测）
    background_tasks.add_task(
        probe_service.run_probe, session, project_id, connection_id, request.app.state.settings
    )
    return ConnectionTestResponse(status="TESTING", started_at=started_at)


@router.patch(
    "/projects/{project_id}/connections/{connection_id}", response_model=ConnectionResponse
)
async def update_connection_policy(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: ConnectionPolicyUpdateRequest,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> ConnectionResponse:
    """更新轮换策略（FR-11；写操作需 ADMIN）。"""
    connection = await policy_service.update_policy(
        session, project_id=project_id, connection_id=connection_id, payload=payload
    )
    return _to_response(connection)


# ============================================================
# 凭据治理端点（T-06；FR-07/08/09，均挂 connections 之下，写操作需 ADMIN）
# ============================================================


def _rotation_response(rotation: SecretRotationRequest) -> RotationResponse:
    """轮换响应：只含状态与引用，绝不含凭据（AC-02）。"""
    return RotationResponse(
        id=rotation.id,
        connection_id=rotation.connection_id,
        status=rotation.status,
        new_secret_ref=rotation.new_secret_ref,
        requested_by=rotation.requested_by,
        approved_by=rotation.approved_by,
        failure_reason=rotation.failure_reason,
        created_at=rotation.created_at,
    )


def _rotation_list_view(rotation: SecretRotationRequest) -> RotationListView:
    """轮换列表视图（前端契约口径；绝不含凭据，AC-02）。

    后端无逐阶段时间列：`staged_at` 由 `created_at` 派生（申请即暂存），
    `activated_at` 仅在 SUCCEEDED 时由 `completed_at` 派生，
    `validated_at` / `rolled_back_at` 如实回 None（不伪造时间轴）。
    """
    return RotationListView(
        id=rotation.id,
        connection_id=rotation.connection_id,
        status=rotation.status,
        reason=rotation.reason,
        staged_at=rotation.created_at,
        validated_at=None,
        activated_at=rotation.completed_at
        if rotation.status == rotation_service.SUCCEEDED
        else None,
        rolled_back_at=None,
    )


@router.post(
    "/projects/{project_id}/connections/{connection_id}/rotations",
    status_code=201,
    response_model=RotationResponse,
)
async def create_rotation(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: RotationCreateRequest,
    request: Request,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> RotationResponse:
    """申请凭据轮换（FR-07：新凭据暂存，VALIDATING；激活前旧凭据继续可用）。"""
    rotation = await rotation_service.create_rotation(
        session,
        project_id=project_id,
        connection_id=connection_id,
        new_username=payload.new_username,
        new_password=payload.new_password,
        reason=payload.reason,
        requested_by=actor.user_id,
        settings=request.app.state.settings,
    )
    return _rotation_response(rotation)


@router.get(
    "/projects/{project_id}/connections/{connection_id}/rotations",
    response_model=list[RotationListView],
)
async def list_rotations(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[RotationListView]:
    """列出连接的轮换申请（联调修复新增；只含状态与引用，绝不含凭据，AC-02）。

    连接不存在或不属于路径项目 → 404（与该路径下其余路由一致，AC-12）。
    分页 + `X-Total-Count` 响应头；列表体为裸数组（与前端消费方式一致）。
    """
    rows, total = await rotation_service.list_rotations_for_connection(
        session, project_id, connection_id, limit=limit, offset=offset
    )
    response.headers["X-Total-Count"] = str(total)
    return [_rotation_list_view(rotation) for rotation in rows]


@router.post(
    "/projects/{project_id}/connections/{connection_id}/rotations/{rotation_id}/validate",
    response_model=RotationResponse,
)
async def validate_rotation(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    rotation_id: uuid.UUID,
    request: Request,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> RotationResponse:
    """暂存凭据连通性验证（FR-07：VALIDATING → WAITING_APPROVAL / FAILED）。"""
    rotation = await rotation_service.validate_rotation(
        session,
        project_id=project_id,
        rotation_id=rotation_id,
        settings=request.app.state.settings,
    )
    return _rotation_response(rotation)


@router.post(
    "/projects/{project_id}/connections/{connection_id}/rotations/{rotation_id}/approve",
    response_model=RotationResponse,
)
async def approve_rotation(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    rotation_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> RotationResponse:
    """审批轮换（FR-07：WAITING_APPROVAL → APPROVED）。"""
    rotation = await rotation_service.approve_rotation(
        session, project_id=project_id, rotation_id=rotation_id, approved_by=actor.user_id
    )
    return _rotation_response(rotation)


@router.post(
    "/projects/{project_id}/connections/{connection_id}/rotations/{rotation_id}/activate",
    response_model=RotationResponse,
)
async def activate_rotation(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    rotation_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> RotationResponse:
    """激活轮换（FR-07：APPROVED → ACTIVATING → SUCCEEDED，切换连接到新凭据）。"""
    await rotation_service.begin_activation(
        session, project_id=project_id, rotation_id=rotation_id
    )
    rotation = await rotation_service.finish_activation(
        session, project_id=project_id, rotation_id=rotation_id
    )
    return _rotation_response(rotation)


@router.post(
    "/projects/{project_id}/connections/{connection_id}/rotations/{rotation_id}/rollback",
    status_code=201,
    response_model=RollbackResponse,
)
async def rollback_rotation(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    rotation_id: uuid.UUID,
    reason: str | None = Query(default=None, max_length=2000),
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> RollbackResponse:
    """回滚轮换（FR-07：未激活即中止，旧凭据原样保留、原始年龄不刷新）。"""
    rollback = await rollback_service.rollback_rotation(
        session,
        project_id=project_id,
        rotation_id=rotation_id,
        requested_by=actor.user_id,
        reason=reason,
    )
    return RollbackResponse(
        id=rollback.id,
        rotation_request_id=rollback.rotation_request_id,
        status=rollback.status,
        reason=rollback.reason,
        created_at=rollback.created_at,
    )


@router.get(
    "/projects/{project_id}/connections/{connection_id}/rotations/{rotation_id}",
    response_model=RotationResponse,
)
async def get_rotation(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    rotation_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> RotationResponse:
    """轮换详情（FR-07）。"""
    from etl_agent.secrets.rotation import _load_rotation

    rotation = await _load_rotation(session, project_id, rotation_id)
    return _rotation_response(rotation)


@router.post(
    "/projects/{project_id}/connections/{connection_id}/migrations",
    status_code=201,
    response_model=MigrationResponse,
)
async def create_migration(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: MigrationCreateRequest,
    request: Request,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> MigrationResponse:
    """申请 Provider 迁移（FR-08：目标 UUID 申请时冻结）。"""
    migration = await migration_service.create_migration(
        session,
        project_id=project_id,
        connection_id=connection_id,
        target_provider=payload.target_provider,
        reason=payload.reason,
        requested_by=actor.user_id,
        settings=request.app.state.settings,
    )
    return MigrationResponse(
        id=migration.id,
        connection_id=migration.connection_id,
        source_provider=migration.source_provider,
        target_provider=migration.target_provider,
        target_secret_id=migration.target_secret_id,
        status=migration.status,
        created_at=migration.created_at,
    )


@router.post(
    "/projects/{project_id}/connections/{connection_id}/migrations/{migration_id}/stage",
    response_model=MigrationResponse,
)
async def stage_migration(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    migration_id: uuid.UUID,
    request: Request,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> MigrationResponse:
    """暂存到目标 Provider（FR-08：幂等，重试写同一冻结 UUID）。"""
    migration = await migration_service.stage_migration(
        session,
        project_id=project_id,
        migration_id=migration_id,
        settings=request.app.state.settings,
    )
    return MigrationResponse(
        id=migration.id,
        connection_id=migration.connection_id,
        source_provider=migration.source_provider,
        target_provider=migration.target_provider,
        target_secret_id=migration.target_secret_id,
        status=migration.status,
        created_at=migration.created_at,
    )


@router.post(
    "/projects/{project_id}/connections/{connection_id}/migrations/{migration_id}/activate",
    response_model=MigrationResponse,
)
async def activate_migration(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    migration_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> MigrationResponse:
    """完成迁移（FR-08：STAGED → SUCCEEDED，切换到目标 Provider）。"""
    migration = await migration_service.activate_migration(
        session, project_id=project_id, migration_id=migration_id
    )
    return MigrationResponse(
        id=migration.id,
        connection_id=migration.connection_id,
        source_provider=migration.source_provider,
        target_provider=migration.target_provider,
        target_secret_id=migration.target_secret_id,
        status=migration.status,
        created_at=migration.created_at,
    )


@router.post(
    "/projects/{project_id}/connections/{connection_id}/destructions",
    status_code=201,
    response_model=DestructionResponse,
)
async def create_destruction(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: DestructionCreateRequest,
    request: Request,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> DestructionResponse:
    """申请销毁凭据（FR-09：资格判定 + 墓碑，不合格即 REJECTED）。"""
    destruction = await destruction_service.request_destruction(
        session,
        project_id=project_id,
        connection_id=connection_id,
        secret_id=payload.secret_id,
        target_type=payload.target_type,
        reason=payload.reason,
        requested_by=actor.user_id,
        settings=request.app.state.settings,
    )
    return DestructionResponse(
        id=destruction.id,
        secret_id=destruction.secret_id,
        target_type=destruction.target_type,
        eligible=destruction.eligible,
        eligibility_note=destruction.eligibility_note,
        status=destruction.status,
        destroyed_at=destruction.destroyed_at,
        created_at=destruction.created_at,
    )


@router.post(
    "/projects/{project_id}/connections/{connection_id}/destructions/{destruction_id}/execute",
    response_model=DestructionResponse,
)
async def execute_destruction(
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    destruction_id: uuid.UUID,
    request: Request,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> DestructionResponse:
    """执行销毁（FR-09：Provider 删除密钥材料 + 墓碑不含秘密）。"""
    destruction = await destruction_service.execute_destruction(
        session,
        project_id=project_id,
        connection_id=connection_id,
        destruction_id=destruction_id,
        settings=request.app.state.settings,
    )
    return DestructionResponse(
        id=destruction.id,
        secret_id=destruction.secret_id,
        target_type=destruction.target_type,
        eligible=destruction.eligible,
        eligibility_note=destruction.eligibility_note,
        status=destruction.status,
        destroyed_at=destruction.destroyed_at,
        created_at=destruction.created_at,
    )
