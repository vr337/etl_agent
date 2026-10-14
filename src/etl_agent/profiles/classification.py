"""M-D 元数据探查与数据分级 — 字段级 6 级分级推断。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-D / 第 8 章「安全 · 多租户」行
覆盖 FR：FR-15（PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED/SECRET/UNKNOWN 推断）
对应 AC：AC-02（分级只作用于元数据，不接触样本值）

**优先级（S4 提示词 9.2，高到低）**：
1. 显式声明 —— 请求体 `column_classifications[列名]`；
2. 同名传播 —— 同项目其他 Profile 中同列名的最高级（列名是跨表语义线索）；
3. 显式映射传播 —— 请求体 `column_classification_map` 命中的分级；
4. 默认 `UNKNOWN`。

**UNKNOWN 不是「无分级」而是「未知」**：策略侧按保守等级 `CONFIDENTIAL` 参与
（FR-15 / US-09），否则未识别的敏感列会被当作 PUBLIC 静默放行。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import ValidationError
from etl_agent.infrastructure.models import DataConnection, DataProfile

CLASSIFICATION_LEVELS: tuple[str, ...] = (
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "RESTRICTED",
    "SECRET",
    "UNKNOWN",
)

DEFAULT_CLASSIFICATION = "UNKNOWN"

# UNKNOWN 参与策略时的保守等级（FR-15：未识别一律按敏感处理）
CONSERVATIVE_LEVEL_FOR_UNKNOWN = "CONFIDENTIAL"

# 严重度：UNKNOWN 按保守等级 CONFIDENTIAL 的严重度计（2），而非最低值
_CLASSIFICATION_SEVERITY: dict[str, int] = {
    "PUBLIC": 0,
    "INTERNAL": 1,
    "CONFIDENTIAL": 2,
    "RESTRICTED": 3,
    "SECRET": 4,
    "UNKNOWN": 2,
}


def validate_level(level: str) -> str:
    """校验分级取值（6 级枚举）。

    Raises:
        ValidationError: 取值不在 6 级之内（BIZ_400_VALIDATION）。
    """
    if level not in CLASSIFICATION_LEVELS:
        raise ValidationError(
            f"非法分级: {level!r}，可选 {list(CLASSIFICATION_LEVELS)}"
        )
    return level


def effective_level(level: str) -> str:
    """策略侧生效等级：UNKNOWN → CONFIDENTIAL（保守），其余原样。"""
    if level == DEFAULT_CLASSIFICATION:
        return CONSERVATIVE_LEVEL_FOR_UNKNOWN
    return level


def severity(level: str) -> int:
    """分级严重度（UNKNOWN 按保守等级计）。"""
    validate_level(level)
    return _CLASSIFICATION_SEVERITY[level]


def highest_classification(levels: list[str]) -> str:
    """取最高级（按生效等级比较，UNKNOWN 参与比较时按 CONFIDENTIAL）。

    Returns:
        生效等级字符串；空列表返回 PUBLIC（无任何列即无敏感面）。
    """
    if not levels:
        return "PUBLIC"
    top = max(levels, key=severity)
    return effective_level(top)


def infer_level(
    *,
    explicit: str | None,
    same_name: str | None,
    mapped: str | None,
) -> str:
    """按 4 级优先级推断单列分级（S4 提示词 9.2）。"""
    for candidate in (explicit, same_name, mapped):
        if candidate is not None:
            return validate_level(candidate)
    return DEFAULT_CLASSIFICATION


def infer_levels(
    column_names: list[str],
    *,
    explicit: dict[str, str] | None = None,
    same_name: dict[str, str] | None = None,
    mapped: dict[str, str] | None = None,
) -> dict[str, str]:
    """批量推断列分级（供 service 组装 columns）。

    Args:
        column_names: 目标表的列名（保序）。
        explicit: 显式声明（列名 → 分级）。
        same_name: 同名传播（列名 → 同项目其他 Profile 的最高级）。
        mapped: 显式映射传播（列名 → 分级）。

    Returns:
        列名 → 分级（未命中任何来源即 UNKNOWN）。
    """
    explicit = explicit or {}
    same_name = same_name or {}
    mapped = mapped or {}
    return {
        name: infer_level(
            explicit=explicit.get(name),
            same_name=same_name.get(name),
            mapped=mapped.get(name),
        )
        for name in column_names
    }


async def load_same_name_levels(
    session: AsyncSession, project_id: uuid.UUID, column_names: list[str]
) -> dict[str, str]:
    """加载同列名在其他 Profile 的分级（同名传播来源，FR-15）。

    隔离边界为项目（AC-12）：只扫描本项目连接的 Profile，跨项目不参与传播。
    扫描 `data_profiles.columns` 的 JSON 而非新增索引列 —— S4 不新增迁移（DoD-2），
    且 Profile 数量受「一表一 Profile」约束，规模可控。

    Returns:
        列名 → 最高级（按严重度取 max；未出现过的列名不出现在结果中）。
    """
    if not column_names:
        return {}
    wanted = set(column_names)
    result = await session.execute(
        select(DataProfile.columns)
        .join(DataConnection, DataConnection.id == DataProfile.connection_id)
        .where(DataConnection.project_id == project_id)
    )
    found: dict[str, str] = {}
    for columns in result.scalars().all():
        for column in iter_column_entries(columns):
            name = column.get("name")
            level = column.get("classification")
            if not isinstance(name, str) or name not in wanted:
                continue
            if not isinstance(level, str) or level not in CLASSIFICATION_LEVELS:
                continue
            current = found.get(name)
            if current is None or severity(level) > severity(current):
                found[name] = level
    return found


def iter_column_entries(columns: object) -> list[dict[str, object]]:
    """遍历 `data_profiles.columns` 的列条目（容错：非预期形态一律跳过）。

    S4 的存储形态为 `{"approximate_row_count": int, "columns": [...]}`：SPEC 5.2 定义
    `columns` 承载「类型 / 主键 / 近似统计」，而 S1 的 `data_profiles` 没有行数列，
    S4 又不新增迁移（DoD-2），故近似行数随列统计一并落在该 JSON 内。
    """
    entries = columns.get("columns") if isinstance(columns, dict) else columns
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def approximate_row_count_of(columns: object) -> int:
    """从 `data_profiles.columns` 取近似行数（缺失/异常形态返回 0）。"""
    if not isinstance(columns, dict):
        return 0
    value = columns.get("approximate_row_count")
    return int(value) if isinstance(value, int) else 0
