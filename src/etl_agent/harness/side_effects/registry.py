"""M-J Harness 安全执行内核 — 副作用注册清单（9 类）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 5.2 章 harness_side_effect_preparations / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-34（副作用以注册清单判定，非 Agent 自报）、FR-37（9 类副作用同协议）
对应 AC：AC-05（prepare→approve→commit + 单次令牌 + 防重放）

清单是**唯一判定来源**：Tool Broker 只认本表登记的副作用类型；
Agent（或任何调用方）自报的副作用名不在表中一律拒绝（FR-34 / S6 禁止事项 16）。
"""
from __future__ import annotations

from dataclasses import dataclass

# 主执行工具（Pipeline 执行本身也是受管副作用，与 9 类副作用同协议，单独登记）
MAIN_EXECUTION_COMMAND = "PIPELINE_EXECUTE"
MAIN_EXECUTION_TOOL = "pipeline.execute"


@dataclass(frozen=True)
class SideEffectSpec:
    """副作用规格（决定风险分级与是否破坏性）。"""

    side_effect_type: str
    tool_name: str
    description: str
    destructive: bool


SIDE_EFFECTS: tuple[SideEffectSpec, ...] = (
    SideEffectSpec("CANCEL_EXECUTION", "execution.cancel", "取消运行中的执行", True),
    SideEffectSpec("ROLLBACK_DORIS", "doris.rollback", "Doris 表回滚到影子表快照", True),
    SideEffectSpec("CLEANUP_DORIS", "doris.cleanup", "清理过期回滚制品", True),
    SideEffectSpec("MIGRATE_SECRET", "secret.migrate", "凭据迁移（provider 间）", False),
    SideEffectSpec("STAGE_SECRET", "secret.stage", "凭据暂存（新版本候选）", False),
    SideEffectSpec("VALIDATE_SECRET", "secret.validate", "凭据暂存验证", False),
    SideEffectSpec("ACTIVATE_SECRET", "secret.activate", "凭据激活（切换生效版本）", True),
    SideEffectSpec("ROLLBACK_SECRET", "secret.rollback", "凭据回滚到上一版本", True),
    SideEffectSpec("DESTROY_SECRET", "secret.destroy", "凭据销毁（留墓碑）", True),
)

REGISTRY: dict[str, SideEffectSpec] = {item.side_effect_type: item for item in SIDE_EFFECTS}

# 允许经 Tool Broker 下发的命令全集：9 类副作用 + 主执行命令
ALLOWED_COMMANDS: frozenset[str] = frozenset(REGISTRY) | {MAIN_EXECUTION_COMMAND}


def get(side_effect_type: str) -> SideEffectSpec | None:
    """按类型取副作用规格（未登记返回 None）。"""
    return REGISTRY.get(side_effect_type)


def is_registered(command_type: str) -> bool:
    """命令是否在注册清单内（Tool Broker 的唯一判定入口）。"""
    return command_type in ALLOWED_COMMANDS


def is_destructive(command_type: str) -> bool:
    """是否破坏性副作用（PDP `HARNESS-P0-DESTRUCTIVE-OP` 的判定依据）。"""
    spec = REGISTRY.get(command_type)
    return bool(spec and spec.destructive)
