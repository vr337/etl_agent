"""M-H 不可变版本与设计审批 — 版本链状态机。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H / 第 5.2 章 pipeline_versions / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-26（版本链 DRAFT→…→APPROVED/REJECTED，禁原地覆盖）、FR-28（设计审批前置状态）
对应 AC：AC-04（不可变版本冻结）、AC-01（审批职责分离）

两条硬规则（FR-26 / S6 禁止事项 14）：
1. **禁原地覆盖**：进入 `FROZEN_STATUSES` 后不得再修改内容，任何变更必须 `parent_version_id` 派生新版本；
2. 状态迁移只允许走 `TRANSITIONS` 白名单，非法迁移一律 `BIZ_409_STATE_CONFLICT`。
"""
from __future__ import annotations

STATUS_DRAFT = "DRAFT"
STATUS_PROFILING = "PROFILING"
STATUS_GENERATED = "GENERATED"
STATUS_VALIDATED = "VALIDATED"
STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"

ALL_STATUSES: tuple[str, ...] = (
    STATUS_DRAFT,
    STATUS_PROFILING,
    STATUS_GENERATED,
    STATUS_VALIDATED,
    STATUS_WAITING_APPROVAL,
    STATUS_APPROVED,
    STATUS_REJECTED,
)

# 版本链（SPEC 3.2 M-H）：DRAFT→PROFILING→GENERATED→VALIDATED→WAITING_APPROVAL→APPROVED/REJECTED。
# 允许的回退边仅 DRAFT/GENERATED（重新生成）；APPROVED/REJECTED 为终态——要改必须派生新版本。
TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_DRAFT: frozenset({STATUS_PROFILING, STATUS_GENERATED}),
    STATUS_PROFILING: frozenset({STATUS_GENERATED, STATUS_DRAFT}),
    STATUS_GENERATED: frozenset({STATUS_VALIDATED, STATUS_DRAFT}),
    STATUS_VALIDATED: frozenset({STATUS_WAITING_APPROVAL, STATUS_GENERATED}),
    STATUS_WAITING_APPROVAL: frozenset({STATUS_APPROVED, STATUS_REJECTED}),
    STATUS_APPROVED: frozenset(),
    STATUS_REJECTED: frozenset(),
}

# 已冻结：内容不可原地修改（FR-26 红线 / S6 禁止事项 14）
FROZEN_STATUSES: frozenset[str] = frozenset(
    {STATUS_VALIDATED, STATUS_WAITING_APPROVAL, STATUS_APPROVED, STATUS_REJECTED}
)

# 允许在其上发起生成运行的版本状态（S5 吸收 3 收敛）。
# 保留 PROFILING：状态链 PROFILING→GENERATED 要求它是可通行状态，
# 否则 PROFILING 会成为无出口的死状态（该口径差异已记入 S6 阶段报告）。
GENERATABLE_STATUSES: frozenset[str] = frozenset(
    {STATUS_DRAFT, STATUS_PROFILING, STATUS_GENERATED}
)

# 可作为 Prepare 前置的状态：已通过门禁冻结（VALIDATED 及其后续）
PREPARABLE_STATUSES: frozenset[str] = frozenset(
    {STATUS_VALIDATED, STATUS_WAITING_APPROVAL, STATUS_APPROVED}
)


def is_frozen(status: str) -> bool:
    """是否已冻结（不可原地覆盖）。"""
    return status in FROZEN_STATUSES


def can_transition(current: str, target: str) -> bool:
    """状态迁移是否被版本链允许。"""
    return target in TRANSITIONS.get(current, frozenset())


def allowed_targets(current: str) -> frozenset[str]:
    """当前状态的合法后继集合。"""
    return TRANSITIONS.get(current, frozenset())
