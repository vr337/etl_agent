"""Bootstrap 工作区初始化测试（T-04.1 / S2 提示词 10）。

覆盖：合法 201、Token 错 403、未启用 403、二次初始化 409（FR-01 / API-01）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import Project, ProjectMembership
from tests.helpers import TEST_BOOTSTRAP_TOKEN, build_test_app, make_client, make_settings

BOOTSTRAP_URL = "/api/v1/bootstrap"


async def test_bootstrap_valid(app: FastAPI, db_session: AsyncSession) -> None:
    """合法 Token → 201，且项目 + ADMIN 成员关系落库。"""
    async with make_client(app) as client:
        response = await client.post(
            BOOTSTRAP_URL,
            json={"project_name": "首个项目", "admin_subject": "bootstrap-admin"},
            headers={"X-Bootstrap-Token": TEST_BOOTSTRAP_TOKEN},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["project_id"] and body["admin_user_id"]

    result = await db_session.execute(
        select(ProjectMembership.role).where(
            ProjectMembership.project_id == body["project_id"],
            ProjectMembership.user_id == body["admin_user_id"],
        )
    )
    assert result.scalar_one() == "ADMIN"


async def test_bootstrap_invalid_token(app: FastAPI, db_session: AsyncSession) -> None:
    """Token 错误 → 403 BIZ_403_BOOTSTRAP_TOKEN_INVALID，且不创建任何项目。"""
    async with make_client(app) as client:
        response = await client.post(
            BOOTSTRAP_URL,
            json={"project_name": "首个项目", "admin_subject": "bootstrap-admin"},
            headers={"X-Bootstrap-Token": "wrong-token"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "BIZ_403_BOOTSTRAP_TOKEN_INVALID"
    count = await db_session.execute(select(func.count()).select_from(Project))
    assert count.scalar() == 0


async def test_bootstrap_disabled(db_session: AsyncSession) -> None:
    """BOOTSTRAP_ENABLED=false → 403 BIZ_403_BOOTSTRAP_DISABLED。"""
    application = await build_test_app(db_session, bootstrap_enabled=False)
    async with make_client(application) as client:
        response = await client.post(
            BOOTSTRAP_URL,
            json={"project_name": "首个项目", "admin_subject": "bootstrap-admin"},
            headers={"X-Bootstrap-Token": TEST_BOOTSTRAP_TOKEN},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "BIZ_403_BOOTSTRAP_DISABLED"


async def test_bootstrap_idempotent(app: FastAPI, db_session: AsyncSession) -> None:
    """二次初始化 → 409 BIZ_409_STATE_CONFLICT（幂等保护，FR-01）。"""
    payload = {"project_name": "首个项目", "admin_subject": "bootstrap-admin"}
    headers = {"X-Bootstrap-Token": TEST_BOOTSTRAP_TOKEN}
    async with make_client(app) as client:
        first = await client.post(BOOTSTRAP_URL, json=payload, headers=headers)
        second = await client.post(BOOTSTRAP_URL, json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "BIZ_409_STATE_CONFLICT"
    count = await db_session.execute(select(func.count()).select_from(Project))
    assert count.scalar() == 1


async def test_bootstrap_token_too_short_rejected_at_startup() -> None:
    """启用时 Token < 32 字节 → 启动校验抛异常（FR-01 fail-fast）。"""
    from etl_agent.config import ProductionConfigError

    short_token: str = "short"
    settings = make_settings(bootstrap_enabled=True, bootstrap_token=short_token)
    with pytest.raises(ProductionConfigError):
        settings.validate_bootstrap()
