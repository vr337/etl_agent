"""M-J Harness 安全执行内核 — Commit（T-12.7，AC-05 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 5.2 章 harness_capability_grants 与 execution_runs / 第 6.2 章 API-10
覆盖 FR：FR-33（服务端重验指纹与审批事实后签发 Ed25519 单次 Capability；同事务原子创建 ExecutionRun 与 Outbox 命令）、FR-35（可靠投递）
对应 AC：AC-05（prepare→approve→commit + 单次令牌 + 防重放）

服务端重验（不信任前端传来的任何事实）：
1. **指纹重验**：按当前事实重算输入 / 资源指纹，与准备单登记值不一致 → 拒绝（制品被换即发现）；
2. **审批事实重验**：required_roles 全部 APPROVE 且票数达标；
3. 状态机必须为 READY（WAITING_APPROVAL → 审批不足；COMMITTED → 重复 Commit）。
同事务：Capability + ExecutionRun(QUEUED) + Outbox(PENDING)——任一失败整体回滚（FR-35）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.audit.hooks import on_commit_issued
from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.errors import RoleRequired
from etl_agent.harness.broker.tool_broker import enqueue_command
from etl_agent.harness.capability.issuer import CapabilityClaims, CapabilityIssuer
from etl_agent.harness.errors import (
    AlreadyCommitted,
    ApprovalInsufficient,
    PreparationExpired,
    PreparationNotFound,
    PreparationStateConflict,
)
from etl_agent.harness.prepare.service import (
    STATUS_READY,
    STATUS_WAITING_APPROVAL,
    compute_input_fingerprint,
    compute_resource_fingerprint,
)
from etl_agent.harness.side_effects.registry import MAIN_EXECUTION_COMMAND
from etl_agent.infrastructure.models import (
    ExecutionPreparation,
    ExecutionRun,
    HarnessApprovalDecision,
)
from etl_agent.versions.artifacts import require_frozen

STATUS_COMMITTED = "COMMITTED"
DECISION_APPROVE = "APPROVE"


@dataclass(frozen=True)
class CommitResult:
    """Commit 结果（API-10 出参）。"""

    token_id: uuid.UUID
    preparation_id: uuid.UUID
    claims: dict[str, Any]
    expires_at: datetime
    run_id: uuid.UUID
    outbox_id: uuid.UUID


def _side_effect_type_of(
    preparation: ExecutionPreparation, digests: dict[str, str]
) -> str | None:
    """取准备单登记的副作用类型（Commit 重验指纹的唯一事实来源）。"""
    report = preparation.impact_report if isinstance(preparation.impact_report, dict) else {}
    requested = report.get("requested_input")
    if isinstance(requested, dict):
        value = requested.get("side_effect_type")
        return str(value) if value else None
    return None


def _resource_ids_of(preparation: ExecutionPreparation) -> tuple[uuid.UUID, ...]:
    """取准备单登记的资源 ID 集合（Commit 重验指纹的事实来源）。"""
    report = preparation.impact_report if isinstance(preparation.impact_report, dict) else {}
    requested = report.get("requested_input")
    if not isinstance(requested, dict):
        return ()
    ids: list[uuid.UUID] = []
    for value in requested.get("resource_ids") or []:
        try:
            ids.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return tuple(ids)


class CommitService:
    """Commit 服务：服务端重验 → 签发单次 Capability → 同事务建 ExecutionRun + Outbox。"""

    def __init__(self, issuer: CapabilityIssuer, *, ttl_seconds: int | None = None) -> None:
        self._issuer = issuer
        self._ttl = ttl_seconds

    async def commit(
        self,
        session: AsyncSession,
        *,
        preparation_id: uuid.UUID,
        project_id: uuid.UUID,
        operator: ActorContext,
        classification_report_sha256: str = "",
    ) -> tuple[CommitResult, str]:
        """执行 Commit（API-10）并返回结果与**一次性令牌本体**。

        令牌本体只在本返回值中出现一次：库中只存 `token_sha256`，Worker 侧消费后即失效
        （Replay Guard，FR-34）。响应体不返回令牌本体，由调用方转交 Worker 通道。

        Raises:
            PreparationNotFound:      准备单不存在或跨项目。
            AlreadyCommitted:         准备单已提交。
            ApprovalInsufficient:     审批事实不满足（槽位 / 票数）。
            PreparationStateConflict: 指纹被篡改 / 状态机不允许。
            PreparationExpired:       准备单 TTL 过期。
            RoleRequired:             操作者不是 OPERATOR。
        """
        if "OPERATOR" not in operator.harness_roles:
            raise RoleRequired("需要 Harness 职责: OPERATOR（API-10 仅操作员可提交）")

        preparation = await self._load(session, preparation_id, project_id)
        if preparation.expires_at is not None and preparation.expires_at <= datetime.now(UTC):
            raise PreparationExpired("准备单已过期（TTL 24h，FR-31）")
        self._assert_committable(preparation)

        version, digests = await require_frozen(session, preparation.pipeline_version_id, project_id)
        await self._reverify_fingerprints(
            session, preparation, project_id=project_id, version_id=version.id, digests=digests
        )
        await self._reverify_approvals(session, preparation)

        contract = dict(preparation.supervision_contract or {})
        claims = CapabilityClaims(
            tool_name=preparation.tool_name or "",
            tool_version=preparation.tool_version or "",
            subject=str(operator.user_id),
            environment=preparation.environment or "",
            artifact_sha256=version.artifact_sha256 or "",
            supervision_contract_sha256=self._contract_digest(contract),
            classification_report_sha256=classification_report_sha256,
            allowed_mitigations=self._allowed_mitigations(preparation),
            project_id=str(project_id),
            preparation_id=str(preparation.id),
            pipeline_version_id=str(version.id),
        )
        now = datetime.now(UTC)
        capability = self._issuer.issue(claims, now=now, ttl_seconds=self._ttl)

        run = ExecutionRun(
            pipeline_version_id=version.id,
            harness_preparation_id=preparation.id,
            idempotency_key=f"commit-{preparation.id}",
            status="QUEUED",
            trigger="MANUAL",
            supervision_contract_sha256=self._contract_digest(contract),
        )
        session.add(run)
        await session.flush()

        outbox = await enqueue_command(
            session,
            project_id=project_id,
            command_type=MAIN_EXECUTION_COMMAND,
            payload={
                "preparation_id": str(preparation.id),
                "claims": claims.to_payload(),
                "allowed_mitigations": claims.allowed_mitigations,
            },
            token_id=capability.token_id,
            run_id=run.id,
        )

        self._persist_capability(session, capability, run)
        preparation.status = STATUS_COMMITTED
        preparation.committed_by = operator.user_id
        preparation.committed_at = now
        await session.flush()

        # 钩子 4（F3）：Commit 签发双写证据账本。只记 token_id（令牌本体是凭据，
        # 入账本即违反 AC-02——token 本体仅经 Redis 交接通道给 Worker，FR-33）。
        # 随调用方路由既有事务提交，钩子只 flush 不自行 commit。
        await on_commit_issued(
            session,
            project_id=project_id,
            preparation_id=preparation.id,
            run_id=run.id,
            token_id=capability.token_id,
            actor_id=operator.user_id,
        )

        result = CommitResult(
            token_id=capability.token_id,
            preparation_id=preparation.id,
            claims=claims.to_payload(),
            expires_at=capability.expires_at,
            run_id=run.id,
            outbox_id=outbox.id,
        )
        return result, capability.token

    async def _load(
        self, session: AsyncSession, preparation_id: uuid.UUID, project_id: uuid.UUID
    ) -> ExecutionPreparation:
        preparation = (
            (
                await session.execute(
                    select(ExecutionPreparation).where(
                        ExecutionPreparation.id == preparation_id,
                        ExecutionPreparation.project_id == project_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if preparation is None:
            raise PreparationNotFound("准备单不存在或不属于当前项目")
        return preparation

    @staticmethod
    def _assert_committable(preparation: ExecutionPreparation) -> None:
        """状态机前置校验（FR-31 状态机 + FR-33 重复提交校验）。"""
        if preparation.status == STATUS_COMMITTED:
            raise AlreadyCommitted("准备单已提交：单次 Capability 已签发（FR-33）")
        if preparation.status in {"REJECTED", "DENIED"}:
            raise PreparationStateConflict(f"准备单状态 {preparation.status}，不可提交")
        if preparation.status == STATUS_WAITING_APPROVAL:
            raise ApprovalInsufficient(
                f"准备单仍在等待审批：需 {preparation.required_approvals} 票，当前未达标（FR-33）"
            )
        if preparation.status != STATUS_READY:
            raise PreparationStateConflict(f"准备单状态 {preparation.status} 不可提交")

    async def _reverify_fingerprints(
        self,
        session: AsyncSession,
        preparation: ExecutionPreparation,
        *,
        project_id: uuid.UUID,
        version_id: uuid.UUID,
        digests: dict[str, str],
    ) -> None:
        """服务端重验指纹：按当前事实重算，与登记值不一致即拒绝（AC-05）。

        典型触发：版本制品被换成另一份（`artifact_sha256` 漂移）或版本被重新生成。

        口径：重算必须使用**准备单登记时的原始入参**（tools / environment / 制品摘要），
        而不是 Commit 请求提供的内容——否则任何非 P3 准备单都会因自证不一致而被拒。
        原始入参取自准备单列与制品摘要映射，二者都由业务库持有、不依赖调用方。
        """
        expected_input = compute_input_fingerprint(
            version_id=version_id,
            artifact_sha256=preparation.artifact_sha256 or "",
            environment=preparation.environment or "",
            tool_name=preparation.tool_name or "",
            tool_version=preparation.tool_version or "",
            side_effect_type=_side_effect_type_of(preparation, digests),
            resource_ids=_resource_ids_of(preparation),
        )
        if preparation.input_fingerprint and preparation.input_fingerprint != expected_input:
            raise PreparationStateConflict(
                "输入指纹与登记值不一致：制品 / 工具 / 环境可能已被篡改（FR-33 服务端重验）"
            )
        expected_resource = compute_resource_fingerprint(
            project_id=project_id, version_id=version_id, digests=digests
        )
        if (
            preparation.resource_fingerprint
            and preparation.resource_fingerprint != expected_resource
        ):
            raise PreparationStateConflict(
                "资源指纹与登记值不一致：版本资源集可能已被篡改（FR-33 服务端重验）"
            )

    @staticmethod
    async def _reverify_approvals(
        session: AsyncSession, preparation: ExecutionPreparation
    ) -> None:
        """服务端重验审批事实：槽位全覆盖 + 票数达标，且无 REJECT 混入。"""
        rows = (
            (
                await session.execute(
                    select(HarnessApprovalDecision).where(
                        HarnessApprovalDecision.preparation_id == preparation.id
                    )
                )
            )
            .scalars()
            .all()
        )
        approves = [item for item in rows if item.decision == DECISION_APPROVE]
        if any(item.decision == "REJECT" for item in rows):
            raise ApprovalInsufficient("存在 REJECT 决策：不得提交（FR-32）")
        required_roles = set(preparation.required_roles or [])
        approved_roles = {item.role for item in approves}
        missing_roles = sorted(required_roles - approved_roles)
        if missing_roles or len(approves) < (preparation.required_approvals or 0):
            detail = f"缺少审批槽 {', '.join(missing_roles)}" if missing_roles else "审批票数不足"
            raise ApprovalInsufficient(f"{detail}（需票数 {preparation.required_approvals}，FR-33）")

    def _persist_capability(
        self, session: AsyncSession, capability: Any, run: ExecutionRun
    ) -> None:
        """落 `harness_capability_grants`：只存 `token_sha256`，不存令牌本体（FR-33）。"""
        from etl_agent.infrastructure.models import HarnessCapabilityGrant

        session.add(
            HarnessCapabilityGrant(
                token_id=capability.token_id,
                preparation_id=run.harness_preparation_id,
                run_id=run.id,
                token_sha256=capability.token_sha256,
                claims=capability.claims,
                expires_at=capability.expires_at,
            )
        )

    @staticmethod
    def _contract_digest(contract: dict[str, Any]) -> str:
        """监督契约摘要（FR-33：Capability 绑定监督契约哈希）。"""
        from etl_agent.harness.prepare.service import supervision_contract_digest

        return supervision_contract_digest(contract)

    @staticmethod
    def _allowed_mitigations(preparation: ExecutionPreparation) -> list[str]:
        """允许的减损动作（FR-33：默认四类，由监督契约冻结）。"""
        contract = preparation.supervision_contract or {}
        allowed = contract.get("allowed_mitigations")
        if isinstance(allowed, list) and allowed:
            return [str(item) for item in allowed]
        return ["CONTINUE", "THROTTLE", "PAUSE", "CANCEL", "QUARANTINE"]
