"""M-F 生成引擎 — 状态节点：门禁校验调用。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F/M-G / 第 7.1 章主流程时序图 / 第 5.2 章 pipeline_artifacts
覆盖 FR：FR-19/20（门禁 + 引擎检查前置于人工审批）、FR-26（通过后冻结 SHA256）、FR-68（门禁未通过前不进入人工审批）
对应 AC：AC-04（门禁 + 有限修复）

关键约束（S5 提示词 11.7 / 禁止事项 16）：
门禁失败反馈**只传规则 ID + 脱敏摘要**，不传完整 HOCON——
反馈字符串由 `gate.service.build_repair_feedback` 唯一构造。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from etl_agent.gate.service import build_repair_feedback, run_gate
from etl_agent.generation.artifacts import freeze_version_artifacts
from etl_agent.generation.state import (
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    GenerationDeps,
    GenerationState,
)
from etl_agent.infrastructure.models import DataConnection, FileAsset


async def gate_node(state: GenerationState, deps: GenerationDeps) -> dict[str, Any]:
    """门禁节点：14 项契约 + 引擎检查；通过则冻结 SHA256 并转入等审批。

    Returns:
        dict: 状态增量（门禁结果 / 修复反馈 / 状态）。
    """
    candidate = state.get("candidate") or {}
    plan = candidate.get("etl_plan") or {}
    hocon = candidate.get("hocon") or ""
    context = await build_gate_context(deps, state)
    result = await run_gate(plan, hocon, context, engine_checker=deps.engine_checker)

    if result.passed:
        await freeze_version_artifacts(
            deps.session, state["pipeline_version_id"], plan, hocon
        )
        # 机器门禁通过才进入人工审批（FR-68：门禁未通过前不进入人工审批中断）
        return {
            "gate_result": result.to_dict(),
            "repair_feedback": None,
            "status": STATUS_WAITING_APPROVAL,
        }
    return {
        "gate_result": result.to_dict(),
        "repair_feedback": build_repair_feedback(result),
        "status": STATUS_RUNNING,
    }


async def build_gate_context(deps: GenerationDeps, state: GenerationState) -> dict[str, Any]:
    """构造门禁上下文：画像 + 当前项目内的连接与文件资产白名单（FR-66 隔离边界）。

    白名单取自数据库而非候选自报——候选说「连接属于我」不算数，
    只有「项目内确实存在该 connection_id」才放行（GATE-009 / GATE-011）。
    """
    project_id = uuid.UUID(str(state["project_id"]))
    connections = await deps.session.execute(
        select(DataConnection.id).where(DataConnection.project_id == project_id)
    )
    file_assets = await deps.session.execute(
        select(FileAsset.id).where(FileAsset.project_id == project_id)
    )
    return {
        "project_id": str(project_id),
        "source_profile": state.get("source_profile") or {},
        "sink_profile": state.get("sink_profile") or {},
        "connection_ids": [str(item) for item in connections.scalars().all()],
        "file_asset_ids": [str(item) for item in file_assets.scalars().all()],
    }
