"""文件资产上限测试（T-08.1 / FR-13，S4 提示词第十节）。

覆盖：> 上限大小 → 422、> 上限行数 → 422（增量中断）、格式不受支持 → 422，
并验证失败上传不留下半成品文件（否则重传会不断堆积无效文件）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers import SeededWorkspace, built_app, make_client

FILE_CSV = (
    b"order_id,customer_email,amount\n"
    b"1,alice@example.com,10.50\n"
    b"2,bob@example.com,20.00\n"
)


async def _upload(
    client: AsyncClient, project_id: uuid.UUID, filename: str, content: bytes
) -> Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/file-assets",
        files={"file": (filename, content, "application/octet-stream")},
        headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
    )


async def test_size_limit(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """超过大小上限 → 422 BIZ_422_FILE_TOO_LARGE，且不留下半成品文件（FR-13）。"""
    # 用 1 MiB 上限 + 约 1.2 MiB 载荷（避免生成 25 MiB 测试数据）
    oversized = (
        "order_id,note\n" + "".join(f"{index},{'x' * 1000}\n" for index in range(1200))
    ).encode()
    assert len(oversized) > 1024 * 1024

    async with built_app(
        db_session, file_asset_root=asset_root, file_asset_max_size_mb=1
    ) as application, make_client(application) as client:
        response = await _upload(client, seeded.project_a_id, "big.csv", oversized)

    assert response.status_code == 422
    assert response.json()["code"] == "BIZ_422_FILE_TOO_LARGE"
    # 落盘目录必须干净：超限文件在写入过程中就被中断并清理
    assert not list(Path(asset_root).rglob("*.*"))


async def test_row_limit(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """超过行数上限 → 422 BIZ_422_FILE_TOO_MANY_ROWS（增量计数在超限处中断，FR-13）。"""
    four_rows = (
        b"order_id,customer_email\n"
        b"1,alice@example.com\n"
        b"2,bob@example.com\n"
        b"3,carol@example.com\n"
        b"4,dave@example.com\n"
    )
    async with built_app(
        db_session, file_asset_root=asset_root, file_asset_max_rows=3
    ) as application, make_client(application) as client:
        response = await _upload(client, seeded.project_a_id, "orders.csv", four_rows)

    assert response.status_code == 422
    assert response.json()["code"] == "BIZ_422_FILE_TOO_MANY_ROWS"
    assert not list(Path(asset_root).rglob("*.*"))


async def test_unsupported_format(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """未支持格式 → 422 BIZ_422_FILE_FORMAT_UNSUPPORTED（不猜格式硬读，FR-13）。"""
    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        response = await _upload(
            client, seeded.project_a_id, "notes.txt", b"just plain text, not a table"
        )

    assert response.status_code == 422
    assert response.json()["code"] == "BIZ_422_FILE_FORMAT_UNSUPPORTED"


async def test_upload_within_limits(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """上限内的文件正常受理：返回格式 / 行数 / 大小（FR-13 正向路径）。"""
    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        response = await _upload(client, seeded.project_a_id, "orders.csv", FILE_CSV)

    assert response.status_code == 201
    body = response.json()
    assert body["format"] == "CSV"
    assert body["row_count"] == 2
    assert body["size_bytes"] == len(FILE_CSV)
