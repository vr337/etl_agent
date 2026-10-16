"""路由提交语义回归（fix/S10-demo-script 联调修复；照 test_bootstrap.py 先例）。

背景：`get_session` 的 `async with` 在请求结束（FastAPI 依赖 ExitStack 退出，
BackgroundTasks 之后）时会回滚未提交事务。路由若只 flush 不 commit，就会
「返回 201/200 但数据全部丢失」——这是测试盲区（conftest 的 db_session 用
create_savepoint 模式，应用侧 commit 只释放 savepoint，无法靠换连接读取区分），
只能靠**探测行为契约**：用 `commit` 被调用的次数判定路由是否提交。

覆盖 4 处演示路径漏 commit（人类裁决 F1 清单）：
  1. connections/router.py register_connection
  2. profiles/router.py create_profile
  3. connections/router.py /connections/{id}/test 路由层（ TESTING 立即可见）
  4. connections/probe.py run_probe（READY / FAILED 两分支终态落库）
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.connections.probe import run_probe
from tests.helpers import SeededWorkspace, dev_pg_credentials, make_client


def _spy_commits(db_session: AsyncSession) -> list[int]:
    """在 db_session 上挂 commit 探针，返回计数列表（路由与后台任务共用同一会话）。"""
    commit_calls: list[int] = []
    original_commit = db_session.commit

    async def _spy_commit() -> None:
        commit_calls.append(1)
        await original_commit()

    db_session.commit = _spy_commit  # type: ignore[method-assign]
    return commit_calls


async def test_register_connection_commits(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """register_connection 必须显式 commit：只 flush 会被回滚（联调 404 根因）。"""
    commit_calls = _spy_commits(db_session)
    pg = dev_pg_credentials()
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections",
            json={
                "name": f"spy-reg-{uuid.uuid4()}",
                "connector_type": "POSTGRESQL",
                "host": pg["host"],
                "port": pg["port"],
                "database_name": pg["database_name"],
                "username": pg["username"],
                "password": str(pg["password"]),
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201, response.text
    assert commit_calls, (
        "register_connection 路由未调用 session.commit：连接不会落库"
        "（登记 201 但随后 /test 404 BIZ_404_CONNECTION_NOT_FOUND）"
    )


async def test_create_profile_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    profile_source_table: str,
) -> None:
    """create_profile 必须显式 commit：docstring 自称「已落库」但路由只 flush。"""
    connection, _ = seeded_connection
    commit_calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            json={
                "connection_id": str(connection.id),  # type: ignore[attr-defined]
                "endpoint_role": "SOURCE",
                "schema_name": "public",
                "table_name": profile_source_table,
                "sample_limit": 10,
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201, response.text
    assert commit_calls, (
        "create_profile 路由未调用 session.commit：Profile 不会落库"
        "（生成运行随后取不到 profile，恒以 WAITING_INPUT 挂起）"
    )


async def test_connection_test_route_commits(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """/test 两段式提交：路由 commit（TESTING 即时可见）+ run_probe commit（终态落库）。"""
    connection, _ = seeded_connection
    commit_calls = _spy_commits(db_session)
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}/test",  # type: ignore[attr-defined]
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "TESTING"
    assert len(commit_calls) >= 2, (
        "/test 需要至少 2 次 commit（路由 TESTING + 探针终态回写）；"
        f"实际 {len(commit_calls)} 次——终态不会落库，连接将永远停在 TESTING"
    )


async def test_run_probe_commits(
    db_session: AsyncSession, seeded_connection: tuple
) -> None:
    """run_probe 的 READY / FAILED 两个分支都必须 commit：否则终态随会话关闭回滚。"""
    connection, settings = seeded_connection
    commit_calls = _spy_commits(db_session)
    outcome = await run_probe(
        db_session,
        connection.project_id,  # type: ignore[attr-defined]
        connection.id,  # type: ignore[attr-defined]
        settings,  # type: ignore[arg-type]
    )
    assert outcome.status in {"READY", "FAILED"}
    assert commit_calls, (
        "run_probe 未调用 session.commit：探针终态不会落库"
        "（连接停在 TESTING，用户永远等不到 READY/FAILED）"
    )
