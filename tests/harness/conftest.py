"""S6 Harness 共享测试基建：验签后的 PDP / Prepare / Commit 服务与四眼职责分配。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 6.2 章 API-08/09/10
覆盖 FR：FR-29~35（策略内核、执行协议、Tool Broker、Outbox）、FR-32（四眼职责）
对应 AC：AC-01（四眼职责分离）、AC-05（prepare→approve→commit + 单次令牌 + 防重放）

策略包直接用仓库交付物 `configs/policy/harness-v2-20260808.*`（由
`scripts/gen_policy_package.py` 生成），密钥取自人类在 `infra/.env` 配置的 Ed25519 密钥对
——测试不新增任何密钥材料（AC-02：不把密钥写进测试）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.policy.package import PolicyPackage, load_package
from etl_agent.infrastructure.models import ProjectMembership, ProjectRoleGrant
from tests.helpers import (
    SeededWorkspace,
    attach_harness_services,
    build_test_app,
    make_settings,
)


@pytest.fixture
def harness_package() -> PolicyPackage:
    """仓库交付的策略包（加载 + 验签成功是后续用例的前提）。"""
    settings = make_settings()
    return load_package(
        settings.policy_package_path,
        settings.policy_package_signature_path,
        settings.harness_public_key_list,
    )


@pytest.fixture
def harness_public_keys() -> list[str]:
    """人类在 infra/.env 配置的验签公钥（测试不新增密钥材料，AC-02）。"""
    return make_settings().harness_public_key_list


@pytest.fixture
def harness_signing_key() -> str:
    """人类在 infra/.env 配置的签名私钥（测试不新增密钥材料，AC-02）。"""
    return make_settings().harness_signing_key_hex


@pytest.fixture
def harness_app(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> FastAPI:
    """装配 Harness 服务的测试应用（与 `app` fixture 等价，显式表达 Harness 用例意图）。"""
    application = build_test_app(db_session, checkpointer=build_memory_checkpointer())
    attach_harness_services(application)
    return application


def build_memory_checkpointer() -> object:
    """内存 Checkpointer（Harness 测试不需要真实验证 langgraph 状态持久化）。"""
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


@pytest.fixture(autouse=True)
async def harness_roles(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> dict[str, uuid.UUID]:
    """AC-01 三人职责分配（PRD 2.1 两层角色体系；autouse：harness 用例一律需要）。

    - engineer-a：DATA_ENGINEER（Maker，提交） + DATA_APPROVER（用于自批 / 同人两槽用例）
    - admin-a：DATA_APPROVER（Checker 1）
    - admin-b：SECURITY_APPROVER（Checker 2） + OPERATOR（Commit）
    - outsider：加入 A 项目并持 DATA_APPROVER（AC-01 的**第四个独立成员**，
      用于验证「同槽第二位审批人」的 ROLE_TAKEN，替代自申请人）

    `admin-b` 默认只属于项目 B，四眼需要他在项目 A 也有身份，故此处补一条 A 项目成员关系
    （职责分离要求**不同自然人**占不同槽，不能复用 admin-a）。
    `outsider` 同理：默认不属任何项目，补成员关系后才能作为「同槽竞争者」参与用例。

    autouse 的原因：审批 / Commit 的职责缺失表现为 403 而非断言失败，
    「显式请求 fixture」的写法一旦漏传就会误导排查方向；此处一律授予更稳妥。
    """
    db_session.add(
        ProjectMembership(
            project_id=seeded.project_a_id,
            user_id=seeded.admin_b_user_id,
            role="VIEWER",
        )
    )
    db_session.add(
        ProjectMembership(
            project_id=seeded.project_a_id,
            user_id=seeded.outsider_user_id,
            role="VIEWER",
        )
    )
    db_session.add_all(
        [
            ProjectRoleGrant(
                project_id=seeded.project_a_id,
                user_id=seeded.engineer_a_user_id,
                role="DATA_ENGINEER",
                granted_by=seeded.admin_a_user_id,
            ),
            ProjectRoleGrant(
                project_id=seeded.project_a_id,
                user_id=seeded.engineer_a_user_id,
                role="DATA_APPROVER",
                granted_by=seeded.admin_a_user_id,
            ),
            ProjectRoleGrant(
                project_id=seeded.project_a_id,
                user_id=seeded.admin_a_user_id,
                role="DATA_APPROVER",
                granted_by=seeded.admin_a_user_id,
            ),
            ProjectRoleGrant(
                project_id=seeded.project_a_id,
                user_id=seeded.admin_b_user_id,
                role="SECURITY_APPROVER",
                granted_by=seeded.admin_a_user_id,
            ),
            ProjectRoleGrant(
                project_id=seeded.project_a_id,
                user_id=seeded.outsider_user_id,
                role="DATA_APPROVER",
                granted_by=seeded.admin_a_user_id,
            ),
            ProjectRoleGrant(
                project_id=seeded.project_a_id,
                user_id=seeded.admin_b_user_id,
                role="OPERATOR",
                granted_by=seeded.admin_a_user_id,
            ),
        ]
    )
    await db_session.flush()
    return {
        "maker": seeded.engineer_a_user_id,
        "data_approver": seeded.admin_a_user_id,
        "security_approver": seeded.admin_b_user_id,
        "operator": seeded.admin_b_user_id,
        "slot_rival": seeded.outsider_user_id,
    }


@pytest.fixture
def tampered_package_path(tmp_path: Path) -> Path:
    """篡改后的策略包（内容改一个字符、签名原样）：用于验签失败用例。"""
    settings = make_settings()
    source = Path(settings.policy_package_path)
    target = tmp_path / "tampered.json"
    target.write_text(source.read_text(encoding="utf-8").replace("harness-v2", "harness-vX", 1), encoding="utf-8")
    return target
