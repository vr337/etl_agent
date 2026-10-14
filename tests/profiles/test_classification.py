"""字段级 6 级分级推断测试（T-07.5 / FR-15，S4 提示词第十节）。

覆盖：显式声明优先、同名传播、显式映射传播、默认 UNKNOWN 保守参与、
以及同名传播的库内来源（同项目 Profile）。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import DataProfile
from etl_agent.profiles import classification
from tests.helpers import SeededWorkspace


def test_classification_explicit() -> None:
    """优先级 1：显式声明压过同名传播与映射传播（FR-15）。"""
    level = classification.infer_level(
        explicit="SECRET", same_name="RESTRICTED", mapped="PUBLIC"
    )
    assert level == "SECRET"


def test_classification_propagation() -> None:
    """优先级 2：同名传播压过显式映射传播（FR-15）。"""
    level = classification.infer_level(explicit=None, same_name="RESTRICTED", mapped="PUBLIC")
    assert level == "RESTRICTED"


def test_classification_mapping() -> None:
    """优先级 3：显式映射传播压过默认值（FR-15）。"""
    level = classification.infer_level(explicit=None, same_name=None, mapped="INTERNAL")
    assert level == "INTERNAL"


def test_classification_default_unknown() -> None:
    """优先级 4：无任何来源 → UNKNOWN，且按保守等级 CONFIDENTIAL 参与策略（FR-15）。"""
    level = classification.infer_level(explicit=None, same_name=None, mapped=None)
    assert level == classification.DEFAULT_CLASSIFICATION
    assert classification.effective_level(level) == "CONFIDENTIAL"
    assert classification.severity(level) == classification.severity("CONFIDENTIAL")


def test_classification_highest() -> None:
    """最高级按生效等级比较：UNKNOWN 不被当作最低级放行（FR-15）。"""
    assert classification.highest_classification(["PUBLIC", "UNKNOWN"]) == "CONFIDENTIAL"
    assert classification.highest_classification(["PUBLIC", "INTERNAL"]) == "INTERNAL"
    assert classification.highest_classification([]) == "PUBLIC"


async def test_classification_same_name_from_db(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """同名传播的库内来源：同项目其他 Profile 的同名列分级被继承（FR-15 / AC-12）。"""
    connection, _ = seeded_connection
    db_session.add(
        DataProfile(
            connection_id=connection.id,
            endpoint_role="SOURCE",
            schema_name="public",
            table_name="legacy_orders",
            columns={
                "approximate_row_count": 0,
                "columns": [
                    {
                        "name": "customer_email",
                        "type": "text",
                        "nullable": True,
                        "is_primary_key": False,
                        "null_rate": None,
                        "distinct_estimate": None,
                        "classification": "SECRET",
                        "effective_classification": "SECRET",
                    }
                ],
            },
            masked_samples=[],
            classification_report_sha256="0" * 64,
            expires_at=None,
        )
    )
    await db_session.flush()

    levels = await classification.load_same_name_levels(
        db_session, seeded.project_a_id, ["customer_email", "order_id"]
    )
    assert levels == {"customer_email": "SECRET"}


async def test_classification_cross_project_not_propagated(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection_b: tuple
) -> None:
    """同名传播受项目边界约束：项目 B 的 Profile 不影响项目 A 的推断（AC-12）。"""
    connection_b, _ = seeded_connection_b
    db_session.add(
        DataProfile(
            connection_id=connection_b.id,
            endpoint_role="SOURCE",
            schema_name="public",
            table_name="orders_b",
            columns={
                "approximate_row_count": 0,
                "columns": [
                    {
                        "name": "customer_email",
                        "type": "text",
                        "nullable": True,
                        "is_primary_key": False,
                        "null_rate": None,
                        "distinct_estimate": None,
                        "classification": "SECRET",
                        "effective_classification": "SECRET",
                    }
                ],
            },
            masked_samples=[],
            classification_report_sha256="0" * 64,
            expires_at=None,
        )
    )
    await db_session.flush()

    levels = await classification.load_same_name_levels(
        db_session, seeded.project_a_id, ["customer_email"]
    )
    assert levels == {}
