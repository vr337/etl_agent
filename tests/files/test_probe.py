"""文件探查测试（T-08.3 / FR-13/14，S4 提示词第十节）。

覆盖：Schema 推断（类型 / 可空 / 是否脱敏）、样本行已脱敏、非敏感列保持原值。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.profiles.masking import MASK_TOKEN
from tests.helpers import SeededWorkspace, built_app, make_client

FILE_CSV = (
    b"order_id,customer_email,amount,created_at\n"
    b"1,alice@example.com,10.50,2026-10-14\n"
    b"2,bob@example.com,,2026-10-15\n"
)


async def test_probe_masked_samples(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """文件探查：Schema 推断正确，敏感列样本脱敏、非敏感列原值保留（FR-13/14）。"""
    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        uploaded = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets",
            files={"file": ("orders.csv", FILE_CSV, "text/csv")},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert uploaded.status_code == 201
        asset_id = uploaded.json()["id"]

        probed = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets/{asset_id}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert probed.status_code == 200
    body = probed.json()
    assert body["format"] == "CSV"

    columns = {column["name"]: column for column in body["columns"]}
    assert columns["order_id"]["type"] == "integer"
    assert columns["amount"]["type"] == "number"
    assert columns["created_at"]["type"] == "date"
    assert columns["customer_email"]["type"] == "string"
    # 可空性来自样本：第 2 行 amount 为空 → nullable
    assert columns["amount"]["nullable"] is True
    assert columns["order_id"]["nullable"] is False
    # 脱敏标记只落在敏感列上
    assert columns["customer_email"]["masked"] is True
    assert columns["order_id"]["masked"] is False

    assert len(body["masked_samples"]) == 2
    first, second = body["masked_samples"]
    assert first["customer_email"] == MASK_TOKEN
    assert second["customer_email"] == MASK_TOKEN
    # 非敏感列保持原值：证明脱敏是列级而非全量抹除
    assert first["order_id"] == "1"
    assert first["amount"] == "10.50"
    assert second["amount"] is None
