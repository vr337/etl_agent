"""零明文泄露测试（AC-02；S4 提示词第十节，DoD-6 核心）。

覆盖：Profile 响应不含样本明文与凭据明文；落库的 `masked_samples` 同样不含明文；
并先自证「样本明文确实存在于源表」，否则断言会退化为恒真。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import DataProfile
from etl_agent.profiles.masking import MASK_TOKEN
from tests.helpers import SAMPLE_EMAILS, SeededWorkspace, dev_pg_credentials, make_client


async def test_zero_plaintext_profile_response(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    profile_source_table: str,
) -> None:
    """AC-02：Profile 响应与落库样本均不含样本明文 / 凭据明文。"""
    connection, _ = seeded_connection
    password = str(dev_pg_credentials()["password"])

    # 自证：样本明文确实存在于源表（否则下面的「不含明文」断言毫无意义）
    stored = (
        await db_session.execute(
            text(f'SELECT customer_email FROM public."{profile_source_table}" ORDER BY id')
        )
    ).scalars().all()
    assert list(stored) == list(SAMPLE_EMAILS)

    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            json={
                "connection_id": str(connection.id),
                "endpoint_role": "SOURCE",
                "schema_name": "public",
                "table_name": profile_source_table,
                "sample_limit": 10,
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201

    body = response.json()
    # 1) 响应全文不含任何样本明文
    for email in SAMPLE_EMAILS:
        assert email not in response.text
    # 2) 响应全文不含连接密码明文
    assert password not in response.text
    # 3) 敏感列在样本中被整体替换为占位符
    assert body["masked_samples"]
    for row in body["masked_samples"]:
        assert row["customer_email"] == MASK_TOKEN
        assert row["note"].startswith("note-")  # 非敏感列保持原值，证明脱敏是列级而非全量抹除

    # 4) 落库的 masked_samples 同样不含明文（响应与库两处都要干净）
    profile_id = uuid.UUID(body["id"])
    row = (
        await db_session.execute(select(DataProfile).where(DataProfile.id == profile_id))
    ).scalar_one()
    dumped = str(row.masked_samples)
    for email in SAMPLE_EMAILS:
        assert email not in dumped
    assert password not in dumped
