"""凭据到期预警扫描测试（T-06.5 / FR-10，S3 提示词 10）。

覆盖：账龄分级 classify（OVERDUE/WARNING/健康）；scan_credentials 幂等去重；
WARNING 与 OVERDUE 两级生成（账龄取自 Secret.created_at，无需解密，AC-02）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import CredentialExpiryAlert, EncryptedSecret
from etl_agent.secrets import expiry as expiry_service
from etl_agent.secrets.expiry import OVERDUE, WARNING
from tests.helpers import SeededWorkspace, dev_pg_credentials, make_settings


def test_classify_levels() -> None:
    """账龄分级：剩余<0 → OVERDUE；剩余≤warning → WARNING；否则健康（FR-10）。"""
    assert expiry_service.classify(95, 90, 14) == OVERDUE  # 超出最大账龄 5 天
    assert expiry_service.classify(80, 90, 14) == WARNING  # 剩余 10 天 ≤ 14
    assert expiry_service.classify(50, 90, 14) is None  # 健康


def test_classify_boundary_warning_equals_remaining() -> None:
    """剩余天数恰好等于 warning_days → WARNING（含边界）。"""
    # max_age=90, warning=14 → 账龄 76 时剩余 14 == warning → WARNING
    assert expiry_service.classify(76, 90, 14) == WARNING
    # 账龄 75 时剩余 15 > 14 → 健康
    assert expiry_service.classify(75, 90, 14) is None


async def _age_secret(
    session: AsyncSession, secret_id: object, age_days: int
) -> None:
    """把凭据的 created_at 回拨到 age_days 天前（模拟账龄）。"""
    secret = await session.get(EncryptedSecret, secret_id)
    assert secret is not None
    secret.created_at = datetime.now(UTC) - timedelta(days=age_days)
    await session.flush()


async def test_scan_overdue_then_dedupe(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """账龄超 max_age → OVERDUE；二次扫描同连接同级别不重复生成（去重键 UK）。"""
    settings = make_settings()
    pg = dev_pg_credentials()
    from etl_agent.connections.register import register_connection
    from etl_agent.connections.schemas import ConnectionRegisterRequest

    payload = ConnectionRegisterRequest(
        name=f"pg-{__import__('uuid').uuid4()}",
        connector_type="POSTGRESQL",
        host=str(pg["host"]),
        port=int(pg["port"]),  # type: ignore[arg-type]
        database_name=str(pg["database_name"]),
        username=str(pg["username"]),
        password=str(pg["password"]),
    )
    connection = await register_connection(
        db_session, project_id=seeded.project_a_id, payload=payload, settings=settings
    )
    await _age_secret(db_session, connection.secret_ref, age_days=100)

    first = await expiry_service.scan_credentials(db_session)
    assert len(first) == 1
    assert first[0].level == OVERDUE
    assert first[0].credential_age_days == 100

    # 第二次扫描：去重键命中 → 不新建
    second = await expiry_service.scan_credentials(db_session)
    assert second == []

    count = (
        await db_session.execute(select(func.count(CredentialExpiryAlert.id)))
    ).scalar_one()
    assert count == 1


async def test_scan_warning_level(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """剩余天数落入预警窗口 → WARNING（未过期）。"""
    settings = make_settings()
    pg = dev_pg_credentials()
    from etl_agent.connections.register import register_connection
    from etl_agent.connections.schemas import ConnectionRegisterRequest

    payload = ConnectionRegisterRequest(
        name=f"pg-{__import__('uuid').uuid4()}",
        connector_type="POSTGRESQL",
        host=str(pg["host"]),
        port=int(pg["port"]),  # type: ignore[arg-type]
        database_name=str(pg["database_name"]),
        username=str(pg["username"]),
        password=str(pg["password"]),
    )
    connection = await register_connection(
        db_session, project_id=seeded.project_a_id, payload=payload, settings=settings
    )
    # 账龄 80 → 剩余 10 ≤ 14 → WARNING
    await _age_secret(db_session, connection.secret_ref, age_days=80)

    alerts = await expiry_service.scan_credentials(db_session)
    assert len(alerts) == 1
    assert alerts[0].level == WARNING
    assert alerts[0].credential_age_days == 80


async def test_scan_healthy_skipped(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """健康凭据（剩余充足）→ 不生成任何预警。"""
    settings = make_settings()
    pg = dev_pg_credentials()
    from etl_agent.connections.register import register_connection
    from etl_agent.connections.schemas import ConnectionRegisterRequest

    payload = ConnectionRegisterRequest(
        name=f"pg-{__import__('uuid').uuid4()}",
        connector_type="POSTGRESQL",
        host=str(pg["host"]),
        port=int(pg["port"]),  # type: ignore[arg-type]
        database_name=str(pg["database_name"]),
        username=str(pg["username"]),
        password=str(pg["password"]),
    )
    connection = await register_connection(
        db_session, project_id=seeded.project_a_id, payload=payload, settings=settings
    )
    # 账龄 10 → 剩余 80 > 14 → 健康
    await _age_secret(db_session, connection.secret_ref, age_days=10)

    alerts = await expiry_service.scan_credentials(db_session)
    assert alerts == []
