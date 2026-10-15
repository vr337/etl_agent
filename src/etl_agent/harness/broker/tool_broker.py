"""M-J Harness 安全执行内核 — Tool Broker（副作用唯一出口）+ 事务 Outbox 写入。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 3.2 章 M-J 边界说明 / 第 8 章「高可用 · Outbox」
覆盖 FR：FR-34（所有副作用的唯一出口；以注册清单判定，非 Agent 自报）、FR-35（业务事实与 Worker 命令同事务落库）
对应 AC：AC-05（单次令牌 + 防重放）、AC-06/08 前置

S6 边界（提示词 8.7 / 禁止事项 23、24）：
- **S6 不执行副作用**：Tool Broker 的「出口」是**逻辑出口**——原子写入 Outbox（PENDING）；
- 真实执行由 S7 Worker 完成（拉取 Outbox → 验签 + Replay Guard → 调 SeaTunnel）；
- 副作用类型**一律以注册清单判定**（Agent 自报的副作用名不在清单内 → 拒绝）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.errors import (
    CapabilityInvalid,
    PolicyDeny,
    PreparationStateConflict,
    ToolNotRegistered,
)
from etl_agent.harness.side_effects.registry import (
    MAIN_EXECUTION_COMMAND,
    is_registered,
)
from etl_agent.infrastructure.models import (
    ExecutionPreparation,
    HarnessCommandOutbox,
)

STATUS_PENDING = "PENDING"


@dataclass(frozen=True)
class BrokerCommand:
    """Tool Broker 下发命令（副作用目标 + 关联事实）。"""

    command_type: str
    project_id: uuid.UUID
    preparation_id: uuid.UUID
    payload: dict[str, Any] = field(default_factory=dict)


class ToolBroker:
    """副作用唯一出口：注册清单校验 → 令牌消费 → Outbox 原子写入。

    调用方（S7 Worker / 端点）只能经本类触发副作用；直接写 Outbox 或执行命令均视为越权
    （SPEC 3.2 M-J 边界说明：M-J 是唯一被授权触发副作用的模块）。
    """

    async def dispatch(
        self,
        session: AsyncSession,
        *,
        command: BrokerCommand,
        token_id: uuid.UUID,
        token_sha256: str,
    ) -> HarnessCommandOutbox:
        """下发命令（逻辑出口）：注册清单校验 + 写 Outbox PENDING。

        Raises:
            ToolNotRegistered:       副作用 / 命令不在注册清单内（FR-34 / 禁止事项 16）。
            PreparationStateConflict: 准备单已 COMMITTED（不可重复下发）。
            PolicyDeny:              准备单已 REJECTED / DENIED。
        """
        if not is_registered(command.command_type):
            raise ToolNotRegistered(
                f"命令 {command.command_type} 不在副作用注册清单内（FR-34：以注册清单判定，非自报）"
            )
        preparation = await self._load_preparation(session, command.preparation_id, command.project_id)
        if preparation.status in {"REJECTED", "DENIED"}:
            raise PolicyDeny(f"准备单状态 {preparation.status}，拒绝下发副作用")
        if preparation.status == "COMMITTED" and command.command_type == MAIN_EXECUTION_COMMAND:
            # Commit 阶段已同事务写过主执行命令；副作用通道的重复下发视为状态冲突
            raise PreparationStateConflict("主执行命令已在 Commit 同事务写入 Outbox")

        return await enqueue_command(
            session,
            project_id=command.project_id,
            command_type=command.command_type,
            payload={
                **command.payload,
                "preparation_id": str(command.preparation_id),
                "token_id": str(token_id),
                "token_sha256_prefix": token_sha256[:12],
            },
        )

    async def _load_preparation(
        self, session: AsyncSession, preparation_id: uuid.UUID, project_id: uuid.UUID
    ) -> ExecutionPreparation:
        result = await session.execute(
            select(ExecutionPreparation).where(
                ExecutionPreparation.id == preparation_id,
                ExecutionPreparation.project_id == project_id,
            )
        )
        preparation = result.scalars().first()
        if preparation is None:
            raise PreparationStateConflict("准备单不存在或不属于当前项目")
        return preparation


async def enqueue_command(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    command_type: str,
    payload: dict[str, Any],
    token_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> HarnessCommandOutbox:
    """原子写入 Outbox 命令（PENDING）——Tool Broker 的逻辑出口，S6 不发送。

    Args:
        session: 请求级数据库会话（必须与业务事实同一事务，FR-35）。
        project_id: 隔离边界。
        command_type: 命令类型（注册清单内）。
        payload: 命令载荷（只含治理事实与引用，不含凭据，AC-02）。
        token_id: 关联的 Capability（S6 起仅供追溯，令牌本体不落库）。
        run_id: 关联的 ExecutionRun（Commit 时创建）。

    Returns:
        HarnessCommandOutbox: PENDING 状态的行（扫描 / 发送在 S7）。
    """
    body = {**payload, "project_id": str(project_id)}
    if token_id is not None:
        body["token_id"] = str(token_id)
    if run_id is not None:
        body["run_id"] = str(run_id)
    row = HarnessCommandOutbox(
        project_id=project_id,
        command_type=command_type,
        payload=body,
        status=STATUS_PENDING,
        retry_count=0,
    )
    session.add(row)
    await session.flush()
    return row


__all__ = ["STATUS_PENDING", "BrokerCommand", "CapabilityInvalid", "ToolBroker", "enqueue_command"]
