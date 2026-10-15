"""副作用注册清单测试（T-12.12；S6 提示词第十二节 tests/harness）。

覆盖（FR-34/37）：9 类副作用全注册、未注册命令被拒、破坏性判定以清单为准。
"""
from __future__ import annotations

from etl_agent.harness.side_effects.registry import (
    ALLOWED_COMMANDS,
    MAIN_EXECUTION_COMMAND,
    REGISTRY,
    SIDE_EFFECTS,
    get,
    is_destructive,
    is_registered,
)


def test_side_effects_9_registered() -> None:
    """9 类副作用全注册（FR-37：CANCEL_EXECUTION / ROLLBACK_DORIS / CLEANUP_DORIS /
    MIGRATE_SECRET / STAGE|VALIDATE|ACTIVATE|ROLLBACK|DESTROY_SECRET）。"""
    assert len(SIDE_EFFECTS) == 9
    assert len(REGISTRY) == 9
    for side_effect_type in (
        "CANCEL_EXECUTION",
        "ROLLBACK_DORIS",
        "CLEANUP_DORIS",
        "MIGRATE_SECRET",
        "STAGE_SECRET",
        "VALIDATE_SECRET",
        "ACTIVATE_SECRET",
        "ROLLBACK_SECRET",
        "DESTROY_SECRET",
    ):
        assert get(side_effect_type) is not None
        assert is_registered(side_effect_type)


def test_tool_broker_registry_only() -> None:
    """Agent 自报的副作用不在注册清单 → 判定未注册（FR-34 / S6 禁止事项 16）。"""
    assert not is_registered("DROP_DATABASE")
    assert not is_registered("TELEPORT_DATA")
    assert not is_registered("")
    # 主执行命令 + 9 类副作用 = 10 个可下发命令
    assert len(ALLOWED_COMMANDS) == 10
    assert MAIN_EXECUTION_COMMAND in ALLOWED_COMMANDS


def test_destructive_judged_by_registry() -> None:
    """破坏性以注册清单为准：CANCEL_EXECUTION → 破坏性；MIGRATE_SECRET → 非破坏性。"""
    assert is_destructive("CANCEL_EXECUTION")
    assert is_destructive("DESTROY_SECRET")
    assert not is_destructive("MIGRATE_SECRET")
    assert not is_destructive("STAGE_SECRET")
    assert not is_destructive("DROP_DATABASE")  # 未登记 → 不按清单判定（PDP 兜底）
