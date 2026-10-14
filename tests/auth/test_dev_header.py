"""dev-header 模式测试（T-03.2 / S2 提示词 10）。

覆盖：合法身份 200、未知 Actor 401、生产启用 dev-header 启动拒绝（AC-11）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI

from etl_agent.config import ProductionConfigError
from etl_agent.main import create_app
from tests.helpers import SeededWorkspace, make_client, make_settings


async def test_dev_header_valid(app: FastAPI, seeded: SeededWorkspace) -> None:
    """合法 X-Actor-ID → 200，且返回该项目的成员列表。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/members",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200
    subjects = {member["subject"] for member in response.json()}
    assert SeededWorkspace.ADMIN_A in subjects


async def test_dev_header_unknown_actor(app: FastAPI, seeded: SeededWorkspace) -> None:
    """未知 Actor（users 无此 subject）→ 401 AUTH_401_UNKNOWN_ACTOR。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/members",
            headers={"X-Actor-ID": "ghost-actor"},
        )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_401_UNKNOWN_ACTOR"


def test_dev_header_disabled_in_prod() -> None:
    """ENV=prod + AUTH_MODE=dev-header → 配置校验抛异常（启动拒绝，AC-11）。"""
    settings = make_settings(env="prod", auth_mode="dev-header")
    with pytest.raises(ProductionConfigError):
        settings.validate_production()


async def test_dev_header_app_startup_refused_in_prod() -> None:
    """ENV=prod + dev-header 时应用 lifespan 启动即抛异常（真实启动路径，AC-11）。"""
    application = create_app(make_settings(env="prod", auth_mode="dev-header"))
    with pytest.raises(ProductionConfigError):
        async with application.router.lifespan_context(application):
            pass
