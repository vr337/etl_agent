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


async def test_router_list_file_assets_empty(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """空列表：项目无文件资产 → 200 + 空数组 + X-Total-Count=0（联调修复新增）。"""
    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


async def test_router_list_file_assets_with_data(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """有数据：返回元数据列表，storage_path 绝不外泄（S4 禁止事项 16，联调修复新增）。"""
    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        uploaded = await _upload(client, seeded.project_a_id, "orders.csv", FILE_CSV)
        assert uploaded.status_code == 201, uploaded.text
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "1"
    body = response.json()
    assert len(body) == 1
    item = body[0]
    assert item["id"] == uploaded.json()["id"]
    # 后端无「原始文件名」列：filename 是落盘 basename（<asset_id>.<ext>），且不含路径分隔
    assert item["filename"].endswith(".csv")
    assert "/" not in item["filename"]
    assert item["upload_status"] == "COMPLETED"
    assert item["format"] == "CSV"
    assert item["row_count"] == 2
    assert item["size_bytes"] > 0
    # 零明文 / 零路径泄露：storage_path 与挂载根目录都不出现在响应中
    assert "storage_path" not in response.text
    assert asset_root not in response.text


async def test_router_list_file_assets_pagination(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """分页：limit/offset 生效，X-Total-Count 反映全量总数（联调修复新增）。"""
    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        for name in ("a.csv", "b.csv"):
            uploaded = await _upload(client, seeded.project_a_id, name, FILE_CSV)
            assert uploaded.status_code == 201, uploaded.text
        path = f"/api/v1/projects/{seeded.project_a_id}/file-assets"
        first_page = await client.get(
            path, params={"limit": 1}, headers={"X-Actor-ID": SeededWorkspace.ADMIN_A}
        )
        second_page = await client.get(
            path, params={"limit": 1, "offset": 1}, headers={"X-Actor-ID": SeededWorkspace.ADMIN_A}
        )
    assert first_page.status_code == 200, first_page.text
    assert first_page.headers["X-Total-Count"] == "2"
    assert len(first_page.json()) == 1
    assert second_page.status_code == 200, second_page.text
    assert second_page.headers["X-Total-Count"] == "2"
    assert len(second_page.json()) == 1
