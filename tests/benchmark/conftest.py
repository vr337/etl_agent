"""S9 Benchmark 用例共享夹具（套件装载 / 已验签 PDP / 角色授予）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（L0/L1/L2 分层评测 + 六项门禁）
对应 AC：AC-09 / AC-14 / AC-15 / AC-16

**不新增密钥材料**：策略包与验签公钥取自仓库交付物 `configs/policy/harness-v2-*.json`
与 `infra/.env` 的人类配置，与 app fixture 同源（AC-02）。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.benchmark.layers.base import BenchmarkSuite, load_suite
from etl_agent.harness.pdp.engine import PdpEngine
from etl_agent.harness.policy.package import load_package
from etl_agent.infrastructure.models import ProjectMembership, ProjectRoleGrant
from tests.helpers import SeededWorkspace, make_settings

# 套件根目录（相对仓库根；pytest 在仓库根运行）
SUITE_ROOT = "benchmarks"


@pytest.fixture
def suite() -> BenchmarkSuite:
    """装载并验签冻结套件（harness-v2/v1）。"""
    return load_suite("harness-v2", "v1", root=SUITE_ROOT)


@pytest.fixture
def pdp_engine() -> PdpEngine:
    """已验签策略包驱动的 PDP 引擎（L0 静态评测用）。"""
    settings = make_settings()
    package = load_package(
        settings.policy_package_path,
        settings.policy_package_signature_path,
        settings.harness_public_key_list,
    )
    return PdpEngine(package)


@pytest.fixture
async def seeded_security_approver(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> uuid.UUID:
    """给 admin-b 授予**项目 A 成员**身份 + SECURITY_APPROVER 职责。

    成员身份与职责是两条独立的闸门（SPEC 6.2）：只有成员才能进入路由，
    只有 SECURITY_APPROVER 才能触发 Benchmark——本夹具把两者同时备齐，
    让「非安全审批人 403」的用例可以直接对比同项目内的其他成员。
    """
    db_session.add(
        ProjectMembership(
            project_id=seeded.project_a_id, user_id=seeded.admin_b_user_id, role="ADMIN"
        )
    )
    db_session.add(
        ProjectRoleGrant(
            project_id=seeded.project_a_id,
            user_id=seeded.admin_b_user_id,
            role="SECURITY_APPROVER",
            granted_by=seeded.admin_a_user_id,
        )
    )
    await db_session.flush()
    return seeded.admin_b_user_id


__all__ = ["SUITE_ROOT"]
