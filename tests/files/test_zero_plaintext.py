"""零明文泄露测试（AC-02；S4 提示词第十节，DoD-6 核心）。

覆盖：上传与探查响应均不含样本明文与存储路径；脱敏样本中敏感列整体替换为占位符。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.profiles.masking import MASK_TOKEN
from tests.helpers import SAMPLE_EMAILS, SeededWorkspace, built_app, make_client


def _csv_with_emails() -> bytes:
    """构造含明文样本的 CSV（首列非敏感，用于对照）。"""
    header = "order_id,customer_email\n"
    body = "".join(
        f"{index},{email}\n" for index, email in enumerate(SAMPLE_EMAILS, start=1)
    )
    return (header + body).encode()


async def test_zero_plaintext_file_response(
    db_session: AsyncSession, seeded: SeededWorkspace, asset_root: str
) -> None:
    """AC-02：文件上传 / 探查响应与脱敏样本均不含明文与存储路径。"""
    payload = _csv_with_emails()

    async with (
        built_app(db_session, file_asset_root=asset_root) as application,
        make_client(application) as client,
    ):
        uploaded = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets",
            files={"file": ("contacts.csv", payload, "text/csv")},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert uploaded.status_code == 201

        probed = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/file-assets/{uploaded.json()['id']}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert probed.status_code == 200

    # 1) 上传响应零明文、零路径
    assert asset_root not in uploaded.text
    assert "storage_path" not in uploaded.text
    # 2) 探查响应零明文、零路径
    assert asset_root not in probed.text
    assert "storage_path" not in probed.text
    for email in SAMPLE_EMAILS:
        assert email not in uploaded.text
        assert email not in probed.text

    # 3) 敏感列在样本中被整体替换；非敏感列保持原值（证明是列级脱敏）
    body = probed.json()
    assert len(body["masked_samples"]) == len(SAMPLE_EMAILS)
    for index, row in enumerate(body["masked_samples"], start=1):
        assert row["customer_email"] == MASK_TOKEN
        assert row["order_id"] == str(index)
