"""M-L 根因诊断 — 9 类失败分类器（FR-47，AC-08 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 execution_runs（failure_category /
failure_stage / retryable / diagnosis）/ 第 8 章「可观测」
覆盖 FR：FR-47（失败分类 CONFIGURATION/CREDENTIALS/CONNECTIVITY/SOURCE_SCHEMA/SINK_WRITE/
ENGINE/OBSERVABILITY/CANCELLATION/UNKNOWN + 失败阶段 + 可重试性 + 修复建议）
对应 AC：AC-08（可解释根因诊断）、AC-02（诊断不含错误栈正文）

**9 类分类与判定来源**（按优先级从「确定的事实」到「猜测」）：

| 分类 | 判定来源 | 可重试 |
|------|---------|--------|
| `CANCELLATION` | 监督硬中断（`failure_category` 已显式写入） | 否（终态不可逆） |
| `CREDENTIALS` | 物化失败 / 鉴权类异常 | 否（要改凭据或契约，重试不会变对） |
| `CONFIGURATION` | HOCON / 契约 / 参数类异常与关键字 | 否 |
| `SOURCE_SCHEMA` | 源结构漂移关键字 | 否（要改版本） |
| `SINK_WRITE` | 目标写入 / 权限类关键字 | 视情况（权限类可重试） |
| `CONNECTIVITY` | 网络 / 超时类异常 | 是 |
| `ENGINE` | 引擎内部错误 / 提交被拒 | 是（引擎侧抖动常见） |
| `OBSERVABILITY` | 遥测 / 日志链路异常 | 是 |
| `UNKNOWN` | 兜底 | 是（默认按可重试，避免把可恢复问题判成永久失败） |

判定顺序：**显式传入的 `failure_category` 优先**（监督链路已判决的事实不被关键字覆盖），
其次异常类型，最后消息关键字。
"""
from __future__ import annotations

from dataclasses import dataclass

from etl_agent.execution.errors import (
    CredentialMaterializationFailed,
    DependencyUnavailable,
    SeatunnelCheckFailed,
    SeatunnelSubmitFailed,
    SeatunnelUnavailable,
    SwapQualityFailed,
    SwapQuarantined,
    SwapSchemaMismatch,
)
from etl_agent.harness.errors import CapabilityExpired, CapabilityInvalid, CapabilityReplayed

# 9 类失败分类（SPEC / 提示词 §8.10）
CATEGORY_CONFIGURATION = "CONFIGURATION"
CATEGORY_CREDENTIALS = "CREDENTIALS"
CATEGORY_CONNECTIVITY = "CONNECTIVITY"
CATEGORY_SOURCE_SCHEMA = "SOURCE_SCHEMA"
CATEGORY_SINK_WRITE = "SINK_WRITE"
CATEGORY_ENGINE = "ENGINE"
CATEGORY_OBSERVABILITY = "OBSERVABILITY"
CATEGORY_CANCELLATION = "CANCELLATION"
CATEGORY_UNKNOWN = "UNKNOWN"

ALL_CATEGORIES: tuple[str, ...] = (
    CATEGORY_CONFIGURATION,
    CATEGORY_CREDENTIALS,
    CATEGORY_CONNECTIVITY,
    CATEGORY_SOURCE_SCHEMA,
    CATEGORY_SINK_WRITE,
    CATEGORY_ENGINE,
    CATEGORY_OBSERVABILITY,
    CATEGORY_CANCELLATION,
    CATEGORY_UNKNOWN,
)

# 异常类型 → (分类, 可重试, 修复建议)
_EXCEPTION_RULES: dict[type[BaseException], tuple[str, bool, str]] = {
    CredentialMaterializationFailed: (
        CATEGORY_CREDENTIALS,
        False,
        (
            "占位符物化失败：核对连接的非密字段与 SecretProvider 中的用户名 / 口令，"
            "并确认 HOCON 使用的变量名与 credential_placeholders.yaml 登记一致"
        ),
    ),
    CapabilityInvalid: (
        CATEGORY_CREDENTIALS,
        False,
        "Capability 验签失败：令牌不可信，需重新 prepare→approve→commit 签发新令牌",
    ),
    CapabilityExpired: (
        CATEGORY_CREDENTIALS,
        False,
        "Capability 已过期：重新执行 Commit 签发新令牌（TTL ≤900s）",
    ),
    CapabilityReplayed: (
        CATEGORY_CREDENTIALS,
        False,
        "Capability 已被消费：单次令牌不可复用，重新走授权链路",
    ),
    SeatunnelCheckFailed: (
        CATEGORY_CONFIGURATION,
        False,
        "引擎 --check 未通过：按脱敏摘要定位 HOCON 语法或连接器参数问题后重新生成版本",
    ),
    SwapSchemaMismatch: (
        CATEGORY_SINK_WRITE,
        False,
        "影子表与正式表 Schema 不一致：核对源 / 目标列定义，重新生成并冻结版本后再发布",
    ),
    SwapQualityFailed: (
        CATEGORY_SINK_WRITE,
        False,
        "质量报告未 PASSED：按报告中的拒绝率与错误码分布定位问题，修正数据或质量契约后重跑",
    ),
    SwapQuarantined: (
        CATEGORY_SINK_WRITE,
        False,
        "运行已被隔离：需人工复盘并解除隔离后才能发布",
    ),
    DependencyUnavailable: (
        CATEGORY_CONNECTIVITY,
        True,
        "外部依赖不可用（Redis / Doris）：确认依赖健康后重试",
    ),
    SeatunnelUnavailable: (
        CATEGORY_CONNECTIVITY,
        True,
        "SeaTunnel 不可达：确认引擎与网络后重试",
    ),
    SeatunnelSubmitFailed: (
        CATEGORY_ENGINE,
        True,
        "引擎拒绝提交：按脱敏摘要核对连接器与资源配置；引擎侧抖动可重试",
    ),
}

# 消息关键字 → (分类, 可重试, 修复建议)（异常类型无法判定时的兜底）
_KEYWORD_RULES: tuple[tuple[tuple[str, ...], str, bool, str], ...] = (
    (
        ("cancellation", "hard interrupt", "监督硬中断", "监督隔离"),
        CATEGORY_CANCELLATION,
        False,
        "运行被监督硬中断：复核冻结预算命中项，调整预算或数据规模后重跑",
    ),
    (
        ("password", "credential", "unauthorized", "authentication", "凭据", "口令", "鉴权"),
        CATEGORY_CREDENTIALS,
        False,
        "鉴权失败：核对凭据有效期与权限，必要时轮换凭据后重跑",
    ),
    (
        ("schema", "column not found", "字段", "列", "类型不匹配", "type mismatch"),
        CATEGORY_SOURCE_SCHEMA,
        False,
        "源结构漂移：重新探查 Profile 并生成新版本（禁止原地改冻结版本）",
    ),
    (
        ("write", "permission denied", "disk full", "写入", "只读"),
        CATEGORY_SINK_WRITE,
        True,
        "目标写入失败：核对目标端权限、空间与写入限流后重试",
    ),
    (
        ("timeout", "connection refused", "unreachable", "network", "超时", "不可达"),
        CATEGORY_CONNECTIVITY,
        True,
        "网络 / 连接类失败：确认两端可达性与连接池配置后重试",
    ),
    (
        ("hocon", "config", "missing parameter", "配置", "参数缺失", "语法"),
        CATEGORY_CONFIGURATION,
        False,
        "配置类失败：核对 HOCON 与连接器参数，重新生成版本后再执行",
    ),
    (
        ("otel", "trace", "logger", "遥测", "日志"),
        CATEGORY_OBSERVABILITY,
        True,
        "可观测链路异常：检查 OTel 导出端点与日志管道；不影响数据面正确性",
    ),
    (
        ("npe", "nullpointer", "internal error", "engine", "引擎"),
        CATEGORY_ENGINE,
        True,
        "引擎内部错误：保留脱敏摘要并联系引擎侧；可先重试一次排除抖动",
    ),
)

# 兜底（UNKNOWN）
_UNKNOWN_RETRYABLE = True
_UNKNOWN_SUGGESTION = "未分类失败：请结合事件流时间线定位；默认按可重试处置，连续失败再人工介入"


@dataclass(frozen=True)
class Classification:
    """一次失败分类结果（可解释：来源 / 分类 / 可重试性 / 修复建议）。"""

    category: str
    retryable: bool
    suggestion: str
    matched_by: str

    def as_evidence(self) -> dict[str, object]:
        """落库 / 事件流形态（不含错误栈正文）。"""
        return {
            "category": self.category,
            "retryable": self.retryable,
            "suggestion": self.suggestion,
            "matched_by": self.matched_by,
        }


def classify(
    *,
    failure_category: str | None = None,
    exception: BaseException | None = None,
    message: str = "",
) -> Classification:
    """分类一次失败（显式分类优先 → 异常类型 → 消息关键字 → UNKNOWN）。

    Args:
        failure_category: 已由上游（如监督链路）显式写入的分类；命中已知分类即直接采用。
        exception: 原始异常（只用于**判定类型**，不读栈正文）。
        message: 摘要消息（用于关键字兜底；调用方需保证已脱敏）。
    """
    explicit = (failure_category or "").strip().upper()
    if explicit in ALL_CATEGORIES:
        return Classification(
            category=explicit,
            retryable=_RETRYABLE_BY_CATEGORY.get(explicit, _UNKNOWN_RETRYABLE),
            suggestion=_SUGGESTION_BY_CATEGORY.get(explicit, _UNKNOWN_SUGGESTION),
            matched_by="explicit",
        )
    if exception is not None:
        for klass in type(exception).__mro__:
            found = _EXCEPTION_RULES.get(klass)
            if found is not None:
                category, retryable, suggestion = found
                return Classification(
                    category=category,
                    retryable=retryable,
                    suggestion=suggestion,
                    matched_by=f"exception:{type(exception).__name__}",
                )
    haystack = message.lower()
    for keywords, category, retryable, suggestion in _KEYWORD_RULES:
        if any(keyword in haystack for keyword in keywords):
            return Classification(
                category=category,
                retryable=retryable,
                suggestion=suggestion,
                matched_by="keyword",
            )
    return Classification(
        category=CATEGORY_UNKNOWN,
        retryable=_UNKNOWN_RETRYABLE,
        suggestion=_UNKNOWN_SUGGESTION,
        matched_by="fallback",
    )


# 显式分类的默认可重试性与建议（分类 → 处置口径）
_RETRYABLE_BY_CATEGORY: dict[str, bool] = {
    CATEGORY_CONFIGURATION: False,
    CATEGORY_CREDENTIALS: False,
    CATEGORY_CONNECTIVITY: True,
    CATEGORY_SOURCE_SCHEMA: False,
    CATEGORY_SINK_WRITE: True,
    CATEGORY_ENGINE: True,
    CATEGORY_OBSERVABILITY: True,
    CATEGORY_CANCELLATION: False,
    CATEGORY_UNKNOWN: True,
}

_SUGGESTION_BY_CATEGORY: dict[str, str] = {
    CATEGORY_CONFIGURATION: "配置类失败：核对 HOCON / 连接器参数 / 契约阈值，重新生成版本后再执行",
    CATEGORY_CREDENTIALS: "凭据类失败：核对凭据有效期与权限，必要时轮换后重跑",
    CATEGORY_CONNECTIVITY: "连接类失败：确认两端可达性与连接池配置后重试",
    CATEGORY_SOURCE_SCHEMA: "源结构漂移：重新探查 Profile 并派生新版本（禁止原地改冻结版本）",
    CATEGORY_SINK_WRITE: "目标写入 / 发布门禁失败：核对目标端权限、空间与质量报告结论",
    CATEGORY_ENGINE: "引擎类失败：保留脱敏摘要并联系引擎侧；可先重试一次排除抖动",
    CATEGORY_OBSERVABILITY: "可观测链路异常：检查 OTel 导出端点与日志管道；不影响数据面正确性",
    CATEGORY_CANCELLATION: "监督硬中断：复核冻结预算命中项，调整预算或数据规模后重跑",
    CATEGORY_UNKNOWN: _UNKNOWN_SUGGESTION,
}


__all__ = [
    "ALL_CATEGORIES",
    "CATEGORY_CANCELLATION",
    "CATEGORY_CONFIGURATION",
    "CATEGORY_CONNECTIVITY",
    "CATEGORY_CREDENTIALS",
    "CATEGORY_ENGINE",
    "CATEGORY_OBSERVABILITY",
    "CATEGORY_SINK_WRITE",
    "CATEGORY_SOURCE_SCHEMA",
    "CATEGORY_UNKNOWN",
    "Classification",
    "classify",
]
