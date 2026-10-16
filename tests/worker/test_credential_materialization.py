"""Secret 物化用例（T-13.6 / AC-02 红线）。

覆盖（FR-67/68）：占位符全量替换、契约外占位符拒绝、缺取值拒绝、
物化取值显式释放、物化后正文不落库不进日志。
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.errors import CredentialMaterializationFailed
from etl_agent.execution.shadow import LoggingShadowTableExecutor
from etl_agent.infrastructure.models import (
    ExecutionEvent,
    ExecutionRun,
    HarnessCommandOutbox,
    QualityReport,
)
from etl_agent.worker.credential_materialization import (
    clear_values,
    find_placeholders,
    load_placeholder_contract,
    render_hocon,
    values_for_connection,
)
from etl_agent.worker.tasks.consume_outbox import scan_outbox_once
from tests.helpers import (
    S7_TEST_HOCON,
    FakeSeaTunnelClient,
    SeededExecution,
    dev_pg_credentials,
    make_settings,
)


def _full_values() -> dict[str, str]:
    """覆盖登记契约全部变量的取值（测试构造值，非真实凭据）。"""
    return {
        "SRC_HOST": "src.internal",
        "SRC_PORT": "5432",
        "SRC_DB": "etl_source",
        "SRC_USER": "etl_reader",
        "SRC_PASSWORD": "src-pass-value",
        "SINK_HOST": "sink.internal",
        "SINK_PORT": "9030",
        "SINK_DB": "dw",
        "SINK_USER": "etl_writer",
        "SINK_PASSWORD": "sink-pass-value",
        "FILE_ASSET_BASE": "/data/file-assets",
    }


def test_contract_registers_all_variables() -> None:
    """契约文件登记的变量集必须与物化实现约定一致（S6 吸收 5 的核对面）。"""
    contract = load_placeholder_contract()
    assert set(contract.variables) == set(_full_values())
    assert set(contract.secret_variables) == {"SRC_PASSWORD", "SINK_PASSWORD"}


def test_credential_materialization_all_vars_replaced() -> None:
    """全部占位符替换完成，且物化后无残留 `${`。"""
    contract = load_placeholder_contract()
    materialized = render_hocon(S7_TEST_HOCON, _full_values(), contract)

    assert find_placeholders(materialized) == set()
    assert "${" not in materialized
    used = find_placeholders(S7_TEST_HOCON)
    assert used == {name for name in _full_values() if name != "FILE_ASSET_BASE"}
    for name in used:
        assert _full_values()[name] in materialized


def test_credential_materialization_rejects_unregistered() -> None:
    """契约外占位符 → 拒绝（宁可失败也不半物化提交引擎）。"""
    contract = load_placeholder_contract()
    with pytest.raises(CredentialMaterializationFailed):
        render_hocon('password = "${ROGUE_SECRET}"', {"ROGUE_SECRET": "x"}, contract)


def test_credential_materialization_rejects_missing_value() -> None:
    """占位符缺取值 → 拒绝，不把空值写进 HOCON。"""
    contract = load_placeholder_contract()
    with pytest.raises(CredentialMaterializationFailed):
        render_hocon(S7_TEST_HOCON, {"SRC_HOST": "h"}, contract)


def test_clear_values_releases_plaintext() -> None:
    """`clear_values` 释放内存中的真实值（AC-02：明文不滞留）。"""
    values = _full_values()
    clear_values(values)
    assert values == {}


def test_values_for_connection_maps_endpoint_role() -> None:
    """非密字段取连接行、用户名与口令取 SecretProvider（连接行不存口令）。"""

    class _Connection:
        host = "db.internal"
        port = 5432
        database_name = "etl_source"
        username = "should-be-overridden"

    values = values_for_connection(
        _Connection(), {"username": "real-user", "password": "real-pass"}, "SRC"
    )
    assert values["SRC_HOST"] == "db.internal"
    assert values["SRC_PORT"] == "5432"
    assert values["SRC_USER"] == "real-user"
    assert values["SRC_PASSWORD"] == "real-pass"
    assert "SINK_HOST" not in values


async def test_materialized_hocon_not_persisted(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """AC-02：全链路跑完后，物化后的明文不得出现在业务库任何治理事实里。"""
    engine = FakeSeaTunnelClient(statuses=["FINISHED"])

    async def _loader(version_id: object) -> dict[str, object] | None:
        return {"etl_plan": seeded_execution.plan, "hocon": seeded_execution.hocon}

    await scan_outbox_once(
        db_session,
        handoff=seeded_execution.handoff,
        engine=engine,
        settings=make_settings(),
        candidate_loader=_loader,  # type: ignore[arg-type]
        shadow_executor=LoggingShadowTableExecutor(),
    )

    password = str(dev_pg_credentials()["password"])
    assert password  # 开发库口令必须存在，否则本用例没有验证力
    assert password in engine.submitted[0]  # 物化确实注入了明文（内存中）

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    outbox = await db_session.get(HarnessCommandOutbox, seeded_execution.outbox_id)
    report = (
        (await db_session.execute(select(QualityReport))).scalars().all()
    )
    events = (await db_session.execute(select(ExecutionEvent))).scalars().all()

    haystack = json.dumps(
        {
            "run": {
                "seatunnel_job_id": run.seatunnel_job_id if run else None,
                "table_metrics": run.table_metrics if run else None,
                "diagnosis": run.diagnosis if run else None,
                "failure_stage": run.failure_stage if run else None,
            },
            "outbox": outbox.payload if outbox else None,
            "reports": [
                {
                    "rules": item.rules,
                    "diagnostics": item.diagnostics,
                    "valid_sink": item.valid_sink,
                    "error_sink": item.error_sink,
                }
                for item in report
            ],
            "events": [item.message for item in events],
        },
        ensure_ascii=False,
        default=str,
    )
    assert password not in haystack


# ---------------------------------------------------------------------------
# T-ENGINE-02：执行视角 host 映射（ETL_AGENT_HOST_MAP）
# ---------------------------------------------------------------------------


def test_values_for_connection_host_map_rewrites_host_and_port() -> None:
    """命中映射：host:port 键同时替换 HOST 与 PORT（物化路径专用）。"""

    class _Connection:
        host = "127.0.0.1"
        port = 13306
        database_name = "source_db"

    values = values_for_connection(
        _Connection(),
        {},
        "SRC",
        host_map={"127.0.0.1:13306": "mysql:3306"},
    )
    assert values["SRC_HOST"] == "mysql"
    assert values["SRC_PORT"] == "3306"
    assert values["SRC_DB"] == "source_db"


def test_values_for_connection_host_map_miss_passes_through() -> None:
    """未命中：原值直通，不报错（映射表只覆盖已知回环地址）。"""

    class _Connection:
        host = "db.internal"
        port = 5432
        database_name = "etl_source"

    values = values_for_connection(
        _Connection(),
        {},
        "SRC",
        host_map={"127.0.0.1:13306": "mysql:3306"},
    )
    assert values["SRC_HOST"] == "db.internal"
    assert values["SRC_PORT"] == "5432"


def test_values_for_connection_host_map_null_safe() -> None:
    """null-safe：host_map 为 None / 空 / host 为 None / port 缺失均不抛。"""

    class _NoHost:
        host = None
        port = None
        database_name = "etl_source"

    class _HostOnly:
        host = "127.0.0.1"
        port = None
        database_name = "source_db"

    for host_map in (None, {}, {"127.0.0.1:13306": "mysql:3306"}):
        values = values_for_connection(_NoHost(), {}, "SRC", host_map=host_map)
        assert "SRC_HOST" not in values  # host 为 None 原样（不进 values）
        values = values_for_connection(_HostOnly(), {}, "SRC", host_map=host_map)
        # port 缺失时键退化为纯 host——命中纯 host 键则替换，未命中直通
        assert values["SRC_HOST"] == (
            "mysql" if (host_map or {}).get("127.0.0.1") else "127.0.0.1"
        )
        assert "SRC_PORT" not in values


def test_values_for_connection_host_map_host_only_key() -> None:
    """纯 host 键（无端口）也能命中替换（映射值可只给主机名）。"""

    class _Connection:
        host = "127.0.0.1"
        port = None
        database_name = "source_db"

    values = values_for_connection(
        _Connection(), {}, "SRC", host_map={"127.0.0.1": "mysql"}
    )
    assert values["SRC_HOST"] == "mysql"
    assert "SRC_PORT" not in values
