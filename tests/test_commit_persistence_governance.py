"""非演示路径 12 处写路由的提交语义回归（T-COMMIT-01 / fix/S10-demo-script 的 F1 续篇）。

## 背景与探针原理

`get_session` 的 `async with` 在请求结束时会回滚未提交事务。路由若只
`session.flush()` 不 `session.commit()`，就会「返回 2xx 但数据全部丢失」。

这是测试盲区：conftest 的 `db_session` 用 `create_savepoint` 模式，
应用侧 commit 只释放 savepoint，**无法靠换连接读取区分**（外层事务仍未提交）。
故与 F1 同款，用 **commit 探针**判定路由是否真的落库 —— 探针挂在
`db_session` 上，app fixture 与测试共享同一会话（见 `app` fixture 的依赖覆盖）。

## 覆盖清单（tracking.md T-COMMIT-01）

- `connections` ×11：
  - `PATCH  /projects/{p}/connections/{c}`（轮换策略）
  - `POST   .../rotations`（申请）/ `.../{r}/validate` / `.../{r}/approve` /
    `.../{r}/activate`（两段式）/ `.../{r}/rollback`
  - `POST   .../migrations`（申请）/ `.../{m}/stage` / `.../{m}/activate`
  - `POST   .../destructions`（申请）/ `.../{d}/execute`
- `files` ×1：`POST /projects/{p}/file-assets`

## 为什么前置状态用 service 直接构造

本文件关心的是**目标路由是否落库**，不是状态机本身（状态机在
`tests/secrets/test_rotation.py` 等已覆盖）。因此轮换 / 迁移 / 销毁的前置态
一律用 service 在同一 `db_session` 上直接构造，然后只对目标路由这一次请求
挂 commit 探针 —— 探针计数为 1 次，语义精确。

`stage_migration` / `activate_migration` 的前置 STAGED 态由 service 造出
PENDING 后直接置位（目标 Provider 是 `vault-kv-v2`，本机无 Vault，
`stage` 的真实外部写入在 `tests/secrets/test_migration.py` 用替身覆盖）。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.secrets import destruction as destruction_service
from etl_agent.secrets import migration as migration_service
from etl_agent.secrets import rotation as rotation_service
from etl_agent.secrets import rotation_rollback as rollback_service
from tests.helpers import (
    SeededWorkspace,
    dev_pg_credentials,
    make_client,
    make_settings,
)
from tests.test_commit_persistence import _spy_commits

_CSV = b"order_id,customer_email\n1,alice@example.com\n2,bob@example.com\n"


def _conn_url(seeded: SeededWorkspace, connection_id: object) -> str:
    return f"/api/v1/projects/{seeded.project_a_id}/connections/{connection_id}"


def _headers() -> dict[str, str]:
    return {"X-Actor-ID": SeededWorkspace.ADMIN_A}


async def _make_rotation(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    connection_id: uuid.UUID,
    *,
    level: str = "validating",
):
    """用 service 造出指定状态的轮换（不走路由，只做前置态）。

    level: validating → WAITING_APPROVAL → APPROVED → ACTIVATING → SUCCEEDED
    """
    settings = make_settings()
    pg = dev_pg_credentials()
    rotation = await rotation_service.create_rotation(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection_id,
        new_username=str(pg["username"]),
        new_password=str(pg["password"]),
        reason="commit-spy setup",
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    if level in {"waiting_approval", "approved", "activating", "succeeded"}:
        rotation = await rotation_service.validate_rotation(
            db_session,
            project_id=seeded.project_a_id,
            rotation_id=rotation.id,
            settings=settings,
        )
    if level in {"approved", "activating", "succeeded"}:
        rotation = await rotation_service.approve_rotation(
            db_session,
            project_id=seeded.project_a_id,
            rotation_id=rotation.id,
            approved_by=seeded.admin_a_user_id,
        )
    if level in {"activating", "succeeded"}:
        await rotation_service.begin_activation(
            db_session, project_id=seeded.project_a_id, rotation_id=rotation.id
        )
    return rotation


# ============================================================
# connections：轮换策略 PATCH
# ============================================================


async def test_update_connection_policy_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """PATCH 连接轮换策略必须 commit：`policy_service.update_policy` 只 flush。"""
    connection, _ = seeded_connection
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.patch(
            _conn_url(seeded, connection.id),
            json={"credential_max_age_days": 120, "credential_warning_days": 30},
            headers=_headers(),
        )
    assert response.status_code == 200, response.text
    assert response.json()["credential_max_age_days"] == 120
    assert calls, "update_connection_policy 路由未调用 session.commit：策略更新返回 200 但数据丢失"


# ============================================================
# connections：凭据轮换 5 处
# ============================================================


async def test_create_rotation_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """申请轮换必须 commit：`rotation_service.create_rotation` 只 flush（且已 store 凭据）。"""
    connection, _ = seeded_connection
    pg = dev_pg_credentials()
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/rotations",
            json={
                "new_username": str(pg["username"]),
                "new_password": str(pg["password"]),
                "reason": "commit-spy",
            },
            headers=_headers(),
        )
    assert response.status_code == 201, response.text
    assert calls, "create_rotation 路由未 commit：凭据已 store 但轮换申请记录丢失（孤儿秘密）"


async def test_validate_rotation_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """暂存校验必须 commit：`validate_rotation` 只 flush。"""
    connection, _ = seeded_connection
    rotation = await _make_rotation(db_session, seeded, connection.id, level="validating")
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/rotations/{rotation.id}/validate",
            headers=_headers(),
        )
    assert response.status_code == 200, response.text
    assert calls, "validate_rotation 路由未 commit：VALIDATING 状态不落库"


async def test_approve_rotation_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """审批轮换必须 commit：`approve_rotation` 只 flush。"""
    connection, _ = seeded_connection
    rotation = await _make_rotation(db_session, seeded, connection.id, level="waiting_approval")
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/rotations/{rotation.id}/approve",
            headers=_headers(),
        )
    assert response.status_code == 200, response.text
    assert calls, "approve_rotation 路由未 commit：审批结论不落库"


async def test_activate_rotation_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """激活轮换必须 commit：begin/finish 两段式都只 flush，路由层统一落库。"""
    connection, _ = seeded_connection
    rotation = await _make_rotation(db_session, seeded, connection.id, level="approved")
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/rotations/{rotation.id}/activate",
            headers=_headers(),
        )
    assert response.status_code == 200, response.text
    assert calls, "activate_rotation 路由未 commit：连接切到新凭据的动作不落库"


async def test_rollback_rotation_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """回滚轮换必须 commit：`rollback_service.rollback_rotation` 只 flush。"""
    connection, _ = seeded_connection
    rotation = await _make_rotation(db_session, seeded, connection.id, level="validating")
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/rotations/{rotation.id}/rollback",
            params={"reason": "commit-spy rollback"},
            headers=_headers(),
        )
    assert response.status_code == 201, response.text
    assert calls, "rollback_rotation 路由未 commit：回滚记录不落库"


# ============================================================
# connections：Provider 迁移 3 处
# ============================================================


async def test_create_migration_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """申请迁移必须 commit：`create_migration` 只 flush。"""
    connection, _ = seeded_connection
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/migrations",
            json={"target_provider": "vault-kv-v2", "reason": "commit-spy"},
            headers=_headers(),
        )
    assert response.status_code == 201, response.text
    assert calls, "create_migration 路由未 commit：冻结的目标 UUID 记录丢失（孤儿秘密）"


async def _make_pending_migration(
    db_session: AsyncSession, seeded: SeededWorkspace, connection_id: uuid.UUID
):
    """造一个 PENDING 迁移（目标 Provider 用 vault-kv-v2；本机无 Vault，走替身置位）。"""
    migration = await migration_service.create_migration(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection_id,
        target_provider="vault-kv-v2",
        reason="commit-spy setup",
        requested_by=seeded.admin_a_user_id,
        settings=make_settings(),
    )
    return migration


async def test_stage_migration_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """暂存迁移必须 commit：`stage_migration` 只 flush（且已向目标 Provider store）。

    目标 Provider 是 `vault-kv-v2`，本机无 Vault（`ETL_AGENT_VAULT_ADDR` 未配置，Q5/S-Q7
    待定）→ 用 fernet 替身顶替目标 Provider。**本测试只关心路由是否落库**，
    暂存的真实外部写入语义在 `tests/secrets/test_migration.py` 覆盖。
    """
    connection, _ = seeded_connection
    migration = await _make_pending_migration(db_session, seeded, connection.id)

    from etl_agent.secrets import migration as migration_module
    from etl_agent.secrets.factory import create_provider

    fernet = create_provider(db_session, make_settings())
    monkeypatch.setattr(migration_module, "create_provider", lambda session, settings: fernet)

    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/migrations/{migration.id}/stage",
            headers=_headers(),
        )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == migration_service.MIGRATION_STAGED
    assert calls, "stage_migration 路由未 commit：STAGED 状态不落库"


async def _make_staged_migration(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    connection_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    """造一个 STAGED 迁移（用 fernet 替身真做一次 stage，目标 secret 因而真实存在）。

    目标 Provider 是 `vault-kv-v2`，本机无 Vault（Q5/S-Q7 待定）→ 替身顶替。
    必须真做 stage：`activate_migration` 会把
    `connection.secret_ref` 指向 `target_secret_id`，该行必须真实存在于
    `encrypted_secrets`，否则 FK 违规（不是被测行为）。
    """
    from etl_agent.secrets import migration as migration_module
    from etl_agent.secrets.factory import create_provider

    fernet = create_provider(db_session, make_settings())
    monkeypatch.setattr(migration_module, "create_provider", lambda session, settings: fernet)

    migration = await migration_service.create_migration(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection_id,
        target_provider="vault-kv-v2",
        reason="commit-spy setup",
        requested_by=seeded.admin_a_user_id,
        settings=make_settings(),
    )
    return await migration_service.stage_migration(
        db_session,
        project_id=seeded.project_a_id,
        migration_id=migration.id,
        settings=make_settings(),
    )


async def test_activate_migration_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完成迁移必须 commit：`activate_migration` 只 flush。"""
    connection, _ = seeded_connection
    migration = await _make_staged_migration(db_session, seeded, connection.id, monkeypatch)
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/migrations/{migration.id}/activate",
            headers=_headers(),
        )
    assert response.status_code == 200, response.text
    assert calls, "activate_migration 路由未 commit：迁移完成状态不落库"


# ============================================================
# connections：凭据销毁 2 处
# ============================================================


async def test_create_destruction_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """申请销毁必须 commit：`request_destruction` 只 flush。"""
    connection, _ = seeded_connection
    rotation = await _make_rotation(db_session, seeded, connection.id, level="waiting_approval")
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/destructions",
            json={
                "secret_id": str(rotation.new_secret_ref),
                "target_type": "ROLLBACK_STAGED",
                "reason": "commit-spy",
            },
            headers=_headers(),
        )
    assert response.status_code == 201, response.text
    assert calls, "create_destruction 路由未 commit：墓碑（资格判定）不落库"


async def test_execute_destruction_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """执行销毁必须 commit：`execute_destruction` 已**真的销毁凭据**，记录丢失最致命。"""
    connection, _ = seeded_connection
    rotation = await _make_rotation(db_session, seeded, connection.id, level="waiting_approval")
    await rollback_service.rollback_rotation(
        db_session,
        project_id=seeded.project_a_id,
        rotation_id=rotation.id,
        requested_by=seeded.admin_a_user_id,
        reason="abandon",
    )
    eligible = await destruction_service.request_destruction(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        secret_id=rotation.new_secret_ref,
        target_type="ROLLBACK_STAGED",
        reason="cleanup staged",
        requested_by=seeded.admin_a_user_id,
        settings=make_settings(),
    )
    assert eligible.eligible is True, eligible.eligibility_note
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"{_conn_url(seeded, connection.id)}/destructions/{eligible.id}/execute",
            headers=_headers(),
        )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DESTROYED"
    assert calls, "execute_destruction 路由未 commit：凭据已销毁但墓碑不落库（不可逆 + 无审计记录）"


# ============================================================
# files：上传 1 处
# ============================================================


async def test_upload_file_asset_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    asset_root: str,
) -> None:
    """上传文件资产必须 commit：`files.service.upload_file` 只 flush。"""
    calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets",
            files={"file": (f"spy-{uuid.uuid4()}.csv", _CSV, "text/csv")},
            headers=_headers(),
        )
    assert response.status_code == 201, response.text
    assert calls, "upload_file_asset 路由未 commit：资产元数据不落库"
