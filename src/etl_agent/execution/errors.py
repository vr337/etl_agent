"""M-K 执行与数据面 — 领域错误码。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-J/M-K / 第 6.1 章 API-11/12
覆盖 FR：FR-38（触发受管执行）、FR-39（运行管理与查询）、FR-42（执行重试）、FR-43（原子 Swap）、FR-44（质量报告）
对应 AC：AC-06（Worker 调度失败的可观测分类）、AC-07（发布门禁的失败类别）、AC-02（错误摘要零明文）

口径（S7 提示词 §8.12 / S8 提示词 §8.14）：
S7 新增 6 个执行/引擎失败类别（已入库 AMEND-006）：
- `BIZ_404_EXECUTION_NOT_FOUND`：执行不存在或跨项目（不泄露存在性）；
- `BIZ_409_EXECUTION_STATE_CONFLICT`：ExecutionRun 状态机不允许当前操作；
- `BIZ_422_SEATUNNEL_SUBMIT_FAILED`：SeaTunnel 作业提交被拒（摘要脱敏）；
- `BIZ_422_SEATUNNEL_CHECK_FAILED`：引擎侧 `--check` 校验未通过；
- `BIZ_422_CREDENTIAL_MATERIALIZATION_FAILED`：占位符物化失败 / 残留；
- `BIZ_503_SEATUNNEL_UNAVAILABLE`：引擎不可达（重试类，非配置错误）。

S8 新增 3 个发布门禁类别（→ AMEND-007 草稿）：
- `BIZ_422_SWAP_SCHEMA_MISMATCH`：影子表与正式表 Schema 不一致（同 Schema 强校验，FR-43）；
- `BIZ_422_SWAP_QUARANTINED`：运行已被 QUARANTINE，禁止原子 Swap（AC-07/AC-08 联动红线）；
- `BIZ_422_SWAP_QUALITY_FAILED`：质量报告未 PASSED，禁止原子 Swap。

复用（不新增）：`BIZ_404_NOT_FOUND`、`BIZ_409_STATE_CONFLICT`（取消 / 重试状态冲突）、
`SYS_503_DEPENDENCY_UNAVAILABLE`（Redis 交接通道 / Doris 不可用）、
`HARNESS_409_CAPABILITY_REPLAYED` 与 `HARNESS_401_CAPABILITY_INVALID`（Worker 侧令牌校验，S6 已定义）。
错误摘要一律不含凭据、HOCON 正文与 Prompt（AC-02）。
"""

from __future__ import annotations

from etl_agent.auth.errors import BizError


class ExecutionNotFound(BizError):
    """执行不存在或跨项目（不泄露存在性，SPEC 6.4 BIZ_404 语义）。"""

    code = "BIZ_404_EXECUTION_NOT_FOUND"
    http_status = 404


class ExecutionStateConflict(BizError):
    """ExecutionRun 状态机不允许当前操作（如终态运行再次取消 / 推进）。"""

    code = "BIZ_409_EXECUTION_STATE_CONFLICT"
    http_status = 409


class SeatunnelSubmitFailed(BizError):
    """SeaTunnel 作业提交失败（错误摘要只含失败分类，不含 HOCON 正文与凭据）。"""

    code = "BIZ_422_SEATUNNEL_SUBMIT_FAILED"
    http_status = 422


class SeatunnelCheckFailed(BizError):
    """SeaTunnel `--check` 校验未通过（只回传脱敏摘要，FR-20）。"""

    code = "BIZ_422_SEATUNNEL_CHECK_FAILED"
    http_status = 422


class SeatunnelUnavailable(BizError):
    """SeaTunnel 引擎不可达（连接 / 超时类失败，可重试）。"""

    code = "BIZ_503_SEATUNNEL_UNAVAILABLE"
    http_status = 503


class CredentialMaterializationFailed(BizError):
    """凭据物化失败：占位符缺失取值、变量不在契约清单或替换后仍有残留。"""

    code = "BIZ_422_CREDENTIAL_MATERIALIZATION_FAILED"
    http_status = 422


class DependencyUnavailable(BizError):
    """外部依赖不可用（复用 SPEC 6.4 `SYS_503_DEPENDENCY_UNAVAILABLE`）。

    Worker 侧典型场景：Capability 交接通道（Redis）不可达——令牌本体无法送达
    Worker 通道时不得静默降级为「无令牌执行」（那等于绕过 Replay Guard）。
    S8 追加场景：目标端 Doris 不可达（原子 Swap / Schema 校验无法执行）。
    """

    code = "SYS_503_DEPENDENCY_UNAVAILABLE"
    http_status = 503


# ============================================================
# S8 新增（→ AMEND-007 草稿）：发布门禁三类失败
# ============================================================


class SwapSchemaMismatch(BizError):
    """影子表与正式表 Schema 不一致（FR-43「同 Schema 强校验」）。

    Swap 会把影子表**替换成**正式表，Schema 不同意味着发布后表结构被静默改写——
    这是数据面最危险的一类失败，必须在 Swap 之前拦下（宁可发布失败也不改表结构）。
    """

    code = "BIZ_422_SWAP_SCHEMA_MISMATCH"
    http_status = 422


class SwapQuarantined(BizError):
    """运行已被 QUARANTINE，禁止原子 Swap（AC-07/AC-08 联动红线）。

    与 `BIZ_422_SWAP_QUALITY_FAILED` 分开设码的原因：隔离是**监督动作**的结果
    （人工/规则判定数据不可信），而质量未过是**契约判定**的结果；两者的处置不同
    （前者要先解除隔离并复盘，后者改质量契约或重跑）。
    """

    code = "BIZ_422_SWAP_QUARANTINED"
    http_status = 422


class SwapQualityFailed(BizError):
    """质量报告未 PASSED（FAILED / WARNING / INCOMPLETE），禁止原子 Swap（FR-43/44）。"""

    code = "BIZ_422_SWAP_QUALITY_FAILED"
    http_status = 422
