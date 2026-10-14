"""M-F 生成引擎 — 状态节点：EtlPlan + HOCON 生成。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 3.2 章 M-D（Profile 结构）/ 第 5.3 章状态隔离
覆盖 FR：FR-18（EtlPlan / HOCON 候选生成）、FR-67（候选正文不落业务库）
对应 AC：AC-04（候选交门禁裁决）、AC-12（画像须属当前项目）

边界（SPEC 1.3 设计原则 1）：
传给 Provider 的画像**只含结构信息**（连接 ID / 表名 / 列名 / 类型 / 分级），
不含凭据、不含脱敏样本——模型永不可见连接密码与样本明文。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.generation.state import STATUS_RUNNING, GenerationDeps, GenerationState
from etl_agent.infrastructure.models import DataConnection, DataProfile
from etl_agent.profiles import classification as classification_service
from etl_agent.profiles.errors import ProfileNotFound


async def generate_node(state: GenerationState, deps: GenerationDeps) -> dict[str, Any]:
    """生成节点：加载画像 → 调用 Provider → 写入候选（正文只进 Checkpoint，FR-67）。

    Returns:
        dict: 状态增量（画像 / 候选 / 候选计数 +1 / 清空修复反馈）。

    Raises:
        ProfileNotFound:      画像不存在或跨项目（BIZ_404_PROFILE_NOT_FOUND，AMEND-003）。
        ValidationError:      必要参数仍缺失（防御：正常流程不会到达）。
        LLMProviderError:     Provider 调用失败（BIZ_422_LLM_PROVIDER_ERROR）。
    """
    source_profile = await load_profile(
        deps.session, state["project_id"], state.get("source_profile_ids") or []
    )
    sink_profile = await load_profile(
        deps.session, state["project_id"], state.get("sink_profile_ids") or []
    )
    candidate = await deps.provider.generate_etl_plan(
        state.get("requirement") or "",
        source_profile,
        sink_profile,
        feedback=state.get("repair_feedback"),
    )
    return {
        "source_profile": source_profile,
        "sink_profile": sink_profile,
        "candidate": {
            "etl_plan": candidate.etl_plan,
            "hocon": candidate.hocon,
            "backend": candidate.backend,
            "model": candidate.model,
        },
        "candidate_count": state.get("candidate_count", 0) + 1,
        "repair_feedback": None,
        "status": STATUS_RUNNING,
    }


async def load_profile(
    session: AsyncSession, project_id: str, profile_ids: list[str]
) -> dict[str, Any]:
    """按 ID 加载 Profile 并归一化为「结构画像」字典。

    查询以 `project_id` 为隔离边界（FR-66）：跨项目画像视同不存在，
    返回 `BIZ_404_PROFILE_NOT_FOUND`（AMEND-003，不泄露存在性）。

    Args:
        session: 请求级数据库会话。
        project_id: 隔离边界（字符串 UUID）。
        profile_ids: 待加载的 Profile ID 列表。

    Returns:
        dict: 多 Profile 合并后的画像（连接 / 表 / 列清单 / 端点角色）。

    Raises:
        ProfileNotFound: 任一 ID 不存在或不属于该项目。
    """
    from etl_agent.auth.errors import ValidationError

    if not profile_ids:
        raise ValidationError("缺少 Profile ID")
    try:
        ids = [uuid.UUID(str(item)) for item in profile_ids]
    except ValueError as exc:
        raise ProfileNotFound("Profile ID 非法或不存在") from exc

    result = await session.execute(
        select(DataProfile, DataConnection)
        .join(DataConnection, DataProfile.connection_id == DataConnection.id)
        .where(
            DataProfile.id.in_(ids),
            DataConnection.project_id == uuid.UUID(str(project_id)),
        )
    )
    rows = result.all()
    if len(rows) != len(ids):
        raise ProfileNotFound("Profile 不存在或不属于当前项目")

    profile, connection = rows[0]
    return {
        "profile_ids": [str(item) for item in ids],
        "connection_id": str(connection.id),
        "connector_type": connection.connector_type,
        "schema_name": profile.schema_name,
        "table_name": profile.table_name,
        "endpoint_role": profile.endpoint_role,
        # S4 吸收 4：columns 为 {approximate_row_count, columns[]} 包裹结构，走访问器读取
        "columns": [
            {
                "name": entry.get("name"),
                "type": entry.get("type"),
                "nullable": entry.get("nullable"),
                "is_primary_key": entry.get("is_primary_key"),
                "classification": entry.get("classification"),
            }
            for entry in classification_service.iter_column_entries(profile.columns)
        ],
        "approximate_row_count": classification_service.approximate_row_count_of(profile.columns),
    }
