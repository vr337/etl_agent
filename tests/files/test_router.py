"""文件资产路由集成测试（T-08.5；S4 提示词第十节）。

覆盖：上传 → 探查闭环、跨项目 403 / 404、存储路径不暴露、资产不存在 404。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import FileAsset
from tests.helpers import SeededWorkspace, built_app, make_client

FILE_CSV = b"order_id,customer_email\n1,alice@example.com\n2,bob@example.com\n"


async def _upload(client, project_id: uuid.UUID, filename: str, content: bytes):  # type: ignore[no-untyped-def]
    return await client.post(
        f"/api/v1/projects/{project_id}/file-assets",
        files={"file": (filename, content, "text/csv")},
        headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
    )


async def test_router_upload_and_probe(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """上传 → 探查闭环：上传回行数，探查回 Schema 与脱敏样本（FR-13/14）。"""
    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        uploaded = await _upload(client, seeded.project_a_id, "orders.csv", FILE_CSV)
        assert uploaded.status_code == 201
        asset = uploaded.json()
        assert asset["row_count"] == 2

        probed = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets/{asset['id']}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert probed.status_code == 200
    assert probed.json()["id"] == asset["id"]


async def test_storage_path_not_in_response(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """上传 / 探查响应都不含 `storage_path`（S4 禁止事项 16：引擎只读挂载路径不暴露）。"""
    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        uploaded = await _upload(client, seeded.project_a_id, "orders.csv", FILE_CSV)
        probed = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets/{uploaded.json()['id']}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    for response in (uploaded, probed):
        assert "storage_path" not in response.text
        assert asset_root not in response.text


async def test_router_cross_project_403(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """非项目成员调用文件资产端点 → 403 AUTH_403_NOT_PROJECT_MEMBER（AC-12）。"""
    async with make_client(app) as client:
        listed = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets",
            files={"file": ("orders.csv", FILE_CSV, "text/csv")},
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
        probed = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets/{uuid.uuid4()}",
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    for response in (listed, probed):
        assert response.status_code == 403
        assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"


async def test_router_cross_project_asset_404(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """项目 A 成员访问项目 B 的资产 → 404（跨项目视同不存在，不泄露存在性）。"""
    asset_b = FileAsset(
        project_id=seeded.project_b_id,
        format="CSV",
        size_bytes=10,
        row_count=1,
        storage_path=f"{asset_root}/{seeded.project_b_id}/placeholder.csv",
    )
    db_session.add(asset_b)
    await db_session.flush()

    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets/{asset_b.id}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_FILE_ASSET_NOT_FOUND"


async def test_router_asset_not_found_404(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """不存在的资产 → 404 BIZ_404_FILE_ASSET_NOT_FOUND。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets/{uuid.uuid4()}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_FILE_ASSET_NOT_FOUND"
