"""M-M 评测与安全进化 — 安全进化（改进提案状态机 + 受控发布）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_improvement_proposals /
safety_releases / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-51（提案状态机不可跳跃、仅人类安全审批人裁决）、FR-52（影子 → 小流量 1%–10% → 晋升 / 回滚）
对应 AC：AC-01（职责分离）、AC-09（受控进化）、AC-02（观察样本脱敏聚合）

模块职责：提案状态机（`proposals`）、受控发布（`release`）、清单签名（`signing`）、
API（`router` / `schemas`）、错误码（`errors`）。
**边界**：提案为非可执行代码；不自动改策略（SPEC 3.2 M-M）。
"""

from etl_agent.safety.errors import (
    SafetyCanaryOutOfRange,
    SafetyProposalNotFound,
    SafetySignatureInvalid,
    SafetyStateConflict,
)
from etl_agent.safety.proposals import (
    PROPOSAL_TYPES,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_IN_REVIEW,
    STATUS_REJECTED,
    STATUS_RELEASED,
    ProposalInput,
    create_proposal,
    decide_proposal,
    get_proposal,
    list_proposals,
    mark_released,
    submit_proposal,
)
from etl_agent.safety.release import (
    CANARY_PERCENT_MAX,
    CANARY_PERCENT_MIN,
    STAGE_CANARY,
    STAGE_PROMOTION_APPROVED,
    STAGE_ROLLED_BACK,
    STAGE_SHADOW_RUNNING,
    ObservationSample,
    get_release_by_proposal,
    promote_to_canary,
    promote_to_production,
    rollback_release,
    start_shadow,
    validate_canary_percent,
)
from etl_agent.safety.signing import (
    ReleaseManifest,
    ReleaseSigner,
    require_valid_release,
    verify_release,
)

__all__ = [
    "CANARY_PERCENT_MAX",
    "CANARY_PERCENT_MIN",
    "PROPOSAL_TYPES",
    "STAGE_CANARY",
    "STAGE_PROMOTION_APPROVED",
    "STAGE_ROLLED_BACK",
    "STAGE_SHADOW_RUNNING",
    "STATUS_APPROVED",
    "STATUS_DRAFT",
    "STATUS_IN_REVIEW",
    "STATUS_REJECTED",
    "STATUS_RELEASED",
    "ObservationSample",
    "ProposalInput",
    "ReleaseManifest",
    "ReleaseSigner",
    "SafetyCanaryOutOfRange",
    "SafetyProposalNotFound",
    "SafetySignatureInvalid",
    "SafetyStateConflict",
    "create_proposal",
    "decide_proposal",
    "get_proposal",
    "get_release_by_proposal",
    "list_proposals",
    "mark_released",
    "promote_to_canary",
    "promote_to_production",
    "require_valid_release",
    "rollback_release",
    "start_shadow",
    "submit_proposal",
    "validate_canary_percent",
    "verify_release",
]
