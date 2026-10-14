"""M-D 元数据探查与数据分级 — Profile 探查服务（编排只读探查全流程）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-04 / 第 3.2 章 M-D / 第 5.2 章 data_profiles
覆盖 FR：FR-12（表级只读 Profile）、FR-14（脱敏样本）、FR-15（6 级分级 + SHA256 报告）
对应 AC：AC-02（脱敏样本与错误摘要零明文）、AC-12（跨项目视同不存在）

编排顺序（S4 提示词 9.1）：加载连接 → 解密凭据（内存短暂持有）→ 读表结构（禁全表 COUNT）
→ 近似统计与采样 → 脱敏 → 分级推断 → 分级报告 SHA256 → 落 data_profiles（TTL 24h）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import ValidationError
from etl_agent.config import Settings
from etl_agent.connections.errors import ConnectorUnsupported, ProbeFailed
from etl_agent.connections.probe import mask_error
from etl_agent.connections.service import load_connection
from etl_agent.infrastructure.models import DataProfile
from etl_agent.profiles import classification as classification_service
from etl_agent.profiles import introspect, statistics
from etl_agent.profiles.errors import SampleLimitOutOfRange
from etl_agent.profiles.introspect import TableNotFound
from etl_agent.profiles.masking import mask_samples
from etl_agent.profiles.report import build_report
from etl_agent.profiles.schemas import (
    ENDPOINT_ROLES,
    PROFILE_TTL_HOURS,
    SAMPLE_LIMIT_MAX,
    SAMPLE_LIMIT_MIN,
    ProfileCreateRequest,
)
from etl_agent.secrets.factory import create_provider

# data_profiles.columns 的存储键（近似行数与列统计同 JSON，见 classification.iter_column_entries）
_COLUMNS_KEY = "columns"
_ROW_COUNT_KEY = "approximate_row_count"


async def create_profile(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    payload: ProfileCreateRequest,
    settings: Settings,
) -> DataProfile:
    """执行表级只读 Profile 探查并落库（API-04 / FR-12/14/15）。

    Args:
        session: 请求级数据库会话。
        project_id: 项目隔离边界（AC-12）。
        payload: 探查请求（连接、端点角色、库表、采样上限、显式分级）。
        settings: 应用配置（SecretProvider 选择、采样上限默认值）。

    Returns:
        DataProfile: 已落库的 Profile（`masked_samples` 已脱敏，绝不含明文）。

    Raises:
        ConnectionNotFound:      连接不存在或跨项目（404，不泄露存在性）。
        ValidationError:         端点角色非法（400）。
        SampleLimitOutOfRange:   采样上限越界（422）。
        ConnectorUnsupported:    连接器类型无探查通道（422）。
        ProbeFailed:             源库探查失败（422，摘要已脱敏）。
    """
    _validate_endpoint_role(payload.endpoint_role)
    sample_limit = _resolve_sample_limit(payload.sample_limit, settings)

    connection = await load_connection(session, project_id, payload.connection_id)
    provider = create_provider(session, settings)
    # 凭据只在内存中短暂持有；禁止写入日志 / 响应 / 数据库（S3 禁止事项 16）
    credentials: dict[str, str] = {}
    if connection.secret_ref is not None:
        credentials = await provider.read(connection.secret_ref)

    try:
        schema = await introspect.read_table_schema(
            connection, credentials, payload.schema_name, payload.table_name
        )
        profile_statistics = await statistics.collect_statistics(
            connection,
            credentials,
            payload.schema_name,
            payload.table_name,
            sample_limit,
        )
    except (ConnectorUnsupported, ValidationError, SampleLimitOutOfRange):
        raise
    except TableNotFound as exc:
        raise ProbeFailed(str(exc)) from exc
    except Exception as exc:
        raise ProbeFailed(
            mask_error(str(exc), [credentials.get("password", ""), credentials.get("username", "")])
        ) from exc

    column_names = [column.name for column in schema.columns]
    same_name_levels = await classification_service.load_same_name_levels(
        session, project_id, column_names
    )
    levels = classification_service.infer_levels(
        column_names,
        explicit=payload.column_classifications,
        same_name=same_name_levels,
        mapped=payload.column_classification_map,
    )

    columns = [
        _build_column(column, profile_statistics.column_stats.get(column.name), levels[column.name])
        for column in schema.columns
    ]
    report = build_report(
        endpoint_role=payload.endpoint_role,
        schema_name=payload.schema_name,
        table_name=payload.table_name,
        columns=columns,
    )
    # 脱敏是落库前的唯一出口（FR-14 / AC-02）：原始行到此为止，不再被引用
    masked = mask_samples(profile_statistics.rows, column_names)

    expires_at = datetime.now(UTC) + timedelta(hours=PROFILE_TTL_HOURS)
    profile = DataProfile(
        connection_id=connection.id,
        endpoint_role=payload.endpoint_role,
        schema_name=payload.schema_name,
        table_name=payload.table_name,
        columns={
            _ROW_COUNT_KEY: profile_statistics.approximate_row_count,
            _COLUMNS_KEY: columns,
        },
        masked_samples=masked,
        classification_report_sha256=report.sha256,
        expires_at=expires_at,
    )
    session.add(profile)
    await session.flush()
    return profile


def _validate_endpoint_role(endpoint_role: str) -> str:
    """校验端点角色（FR-12：SOURCE / SINK）。

    Raises:
        ValidationError: 取值不在 SOURCE/SINK 之内（BIZ_400_VALIDATION）。
    """
    if endpoint_role not in ENDPOINT_ROLES:
        raise ValidationError(
            f"非法端点角色: {endpoint_role!r}，可选 {sorted(ENDPOINT_ROLES)}"
        )
    return endpoint_role


def _resolve_sample_limit(requested: int | None, settings: Settings) -> int:
    """解析采样上限：缺省取配置默认值，越界即拒绝（不做静默截断）。

    Raises:
        SampleLimitOutOfRange: 不在 [1, 100] 内（BIZ_422_SAMPLE_LIMIT_OUT_OF_RANGE）。
    """
    limit = settings.profile_sample_limit_default if requested is None else requested
    if not (SAMPLE_LIMIT_MIN <= limit <= SAMPLE_LIMIT_MAX):
        raise SampleLimitOutOfRange(
            f"sample_limit 须在 [{SAMPLE_LIMIT_MIN}, {SAMPLE_LIMIT_MAX}]，当前 {limit}"
        )
    return limit


def _build_column(
    column: introspect.ColumnInfo,
    stat: statistics.ColumnStat | None,
    level: str,
) -> dict[str, Any]:
    """组装列级 Profile（类型 / 主键 / 近似统计 / 分级）。"""
    return {
        "name": column.name,
        "type": column.data_type,
        "nullable": column.nullable,
        "is_primary_key": column.is_primary_key,
        # 空样本时无统计事实：显式给 None，避免用 0 冒充「无空值」
        "null_rate": stat.null_rate if stat else None,
        "distinct_estimate": stat.distinct_estimate if stat else None,
        "classification": level,
        "effective_classification": classification_service.effective_level(level),
    }
