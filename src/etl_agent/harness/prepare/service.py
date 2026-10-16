"""M-J Harness 安全执行内核 — Prepare 准备单（T-12.5）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J（Prepare）/ 第 5.2 章 execution_preparations / 第 6.2 章 API-08
覆盖 FR：FR-31（冻结输入/资源指纹、影响报告、数据分级、监督契约、回滚计划、TTL 300s–7d 默认 24h、幂等键、无外部副作用）
对应 AC：AC-05（prepare→approve→commit 的第一阶段）

无外部副作用：准备单创建只落库「治理事实」，不触发任何执行（FR-31）。
PDP DENY 时**不落准备单**（FR-29 / 禁止事项 19：无放行通道）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.audit.hooks import on_pdp_decision, on_prepare_created
from etl_agent.harness.errors import IdempotencyConflict
from etl_agent.harness.pdp.engine import PdpEngine, PdpRequest, PolicyDecisionResult
from etl_agent.infrastructure.models import (
    AgentRun,
    DataProfile,
    ExecutionPreparation,
    HarnessPolicyDecision,
    PipelineVersion,
)
from etl_agent.versions.artifacts import require_frozen

# 监督契约默认预算（FR-45 冻结预算：不可扩权硬上限）
DEFAULT_SUPERVISION_CONTRACT: dict[str, Any] = {
    "max_records": 10_000_000,
    "max_bytes": 10 * 1024**3,
    "max_duration_seconds": 3600,
    "max_amplification_ratio": 2.0,
    "max_reject_rate": 0.5,
    "min_quality_rows": 100,
    "warning_ratio": 0.8,
}

DEFAULT_TTL_SECONDS = 24 * 60 * 60
MIN_TTL_SECONDS = 300
MAX_TTL_SECONDS = 7 * 24 * 60 * 60

STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"
STATUS_READY = "READY"
STATUS_DENIED = "DENIED"


@dataclass(frozen=True)
class PrepareInput:
    """API-08 入参（准备单创建所需的治理事实）。"""

    pipeline_version_id: uuid.UUID
    idempotency_key: str
    environment: str
    tool_name: str
    tool_version: str
    actor_project_id: uuid.UUID | None = None
    budget: dict[str, Any] | None = None
    target_schema_aligned: bool | None = None
    classifications: tuple[str, ...] = ()
    side_effect_type: str | None = None
    resource_ids: tuple[uuid.UUID, ...] = ()


def compute_input_fingerprint(
    *,
    version_id: uuid.UUID,
    artifact_sha256: str,
    environment: str,
    tool_name: str,
    tool_version: str,
    side_effect_type: str | None = None,
    resource_ids: tuple[uuid.UUID, ...] = (),
) -> str:
    """输入指纹（FR-31）：Commit 时服务端重验，任何一项漂移即拒绝。"""
    import hashlib
    import json

    payload = {
        "version_id": str(version_id),
        "artifact_sha256": artifact_sha256,
        "environment": environment,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "side_effect_type": side_effect_type,
        "resource_ids": sorted(str(item) for item in resource_ids),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def compute_resource_fingerprint(
    *, project_id: uuid.UUID, version_id: uuid.UUID, digests: dict[str, str]
) -> str:
    """资源指纹（FR-31）：项目 + 版本 + 三类制品摘要。"""
    import hashlib
    import json

    payload = {
        "project_id": str(project_id),
        "version_id": str(version_id),
        "artifact_digests": digests,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def supervision_contract_digest(contract: dict[str, Any]) -> str:
    """监督契约摘要（Capability claims 绑定用）。"""
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(contract, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def resolve_expires_at(requested_ttl: int | None, now: datetime) -> datetime:
    """解析 TTL：默认 24h，夹在 300s–7d（FR-31，禁止隐式放宽）。"""
    ttl = DEFAULT_TTL_SECONDS if requested_ttl is None else requested_ttl
    ttl = max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, ttl))
    return now + timedelta(seconds=ttl)


class PrepareService:
    """准备单服务：PDP 裁决 → 指纹 / 影响报告 / 审核槽位 → 幂等落库。"""

    def __init__(self, pdp_engine: PdpEngine, *, requested_ttl_seconds: int | None = None) -> None:
        self._pdp = pdp_engine
        self._ttl = requested_ttl_seconds

    async def create_preparation(
        self,
        session: AsyncSession,
        *,
        project_id: uuid.UUID,
        payload: PrepareInput,
        requested_by: uuid.UUID,
    ) -> tuple[ExecutionPreparation, PolicyDecisionResult]:
        """创建准备单（API-08）。

        Returns:
            (准备单, PDP 裁决结果)：DENY 时不建单，返回携带裁决结果的 DENIED 单对象视图。

        Raises:
            IdempotencyConflict: 同幂等键不同载荷。
            VersionNotFound:    版本不存在或跨项目（versions 域）。
            ArtifactNotFrozen:  版本未冻结制品。
            PolicyDeny:          PDP DENY（403，无准备单落库）。
        """
        from etl_agent.harness.errors import PolicyDeny

        existing = await self._find_by_idempotency_key(session, project_id, payload.idempotency_key)
        if existing is not None:
            self._assert_same_payload(existing, payload)
            return existing, await self._stored_decision(session, existing)

        version, digests = await require_frozen(session, payload.pipeline_version_id, project_id)
        request = PdpRequest(
            project_id=str(project_id),
            actor_project_id=str(payload.actor_project_id) if payload.actor_project_id else None,
            environment=payload.environment,
            tool_name=payload.tool_name,
            tool_version=payload.tool_version,
            pipeline_version_id=str(version.id),
            artifact_sha256=version.artifact_sha256,
            command_type=payload.side_effect_type,
            side_effect_type=payload.side_effect_type,
            resource_ids=tuple(str(item) for item in payload.resource_ids),
            budget=payload.budget,
            target_schema_aligned=payload.target_schema_aligned,
            classifications=payload.classifications,
        )
        decision = self._pdp.evaluate(request)

        decision_row = HarnessPolicyDecision(
            project_id=project_id,
            outcome=decision.outcome,
            risk_level=decision.risk_level,
            rule_evidence=decision.rule_evidence,
            policy_package_id=decision.policy_package_id,
            policy_package_sha256=decision.policy_package_sha256,
        )
        session.add(decision_row)
        await session.flush()

        # 钩子 1（F3）：PDP 决策双写证据账本——无论 ALLOW / DENY 都必须留痕，
        # 「为什么被拒 / 被放行」是事后复现的唯一依据（FR-29）。DENY 是终局裁决，
        # 路由层会把它转 403 并回滚请求会话，故 DENY 分支在 raise 前单独提交
        # （人类裁决补充 1）；ALLOW 路径的决策行与证据随调用方（路由）同一
        # 事务提交，与 T-COMMIT-01 同族的「flush 不 commit 即丢失」风险由此闭环。
        await on_pdp_decision(
            session,
            project_id=project_id,
            decision_id=decision_row.id,
            outcome=decision.outcome,
            risk_level=decision.risk_level,
            actor_id=requested_by,
        )

        if decision.denied:
            # DENY 特判：决策行 + 证据在本分支显式提交——403 响应路径上请求会话
            # 即将回滚，若不落地则 DENY 留痕随之丢失（留痕是 FR-29 硬要求）。
            await session.commit()
            raise PolicyDeny(
                f"PDP 裁决 DENY（{decision.risk_level}）：{' '.join(
                    item['rule_id'] for item in decision.rule_evidence if item['outcome'] == 'DENY'
                )} 无放行通道"
            )

        now = datetime.now(UTC)
        preparation = ExecutionPreparation(
            project_id=project_id,
            policy_decision_id=decision_row.id,
            pipeline_version_id=version.id,
            status=(
                STATUS_READY if decision.required_approvals == 0 else STATUS_WAITING_APPROVAL
            ),
            risk_level=decision.risk_level,
            environment=payload.environment,
            tool_name=payload.tool_name,
            tool_version=payload.tool_version,
            artifact_sha256=version.artifact_sha256,
            input_fingerprint=compute_input_fingerprint(
                version_id=version.id,
                artifact_sha256=version.artifact_sha256 or "",
                environment=payload.environment,
                tool_name=payload.tool_name,
                tool_version=payload.tool_version,
                side_effect_type=payload.side_effect_type,
                resource_ids=payload.resource_ids,
            ),
            resource_fingerprint=compute_resource_fingerprint(
                project_id=project_id, version_id=version.id, digests=digests
            ),
            impact_report=await self._build_impact_report(
                session,
                project_id=project_id,
                version=version,
                digests=digests,
                payload=payload,
                decision=decision,
            ),
            rollback_plan=self._build_rollback_plan(project_id=project_id, payload=payload),
            supervision_contract=dict(DEFAULT_SUPERVISION_CONTRACT),
            required_roles=decision.required_roles,
            required_approvals=decision.required_approvals,
            idempotency_key=payload.idempotency_key,
            requested_by=requested_by,
            expires_at=resolve_expires_at(self._ttl, now),
        )
        session.add(preparation)
        await session.flush()
        # 钩子 2（F3）：Prepare 创建双写证据账本（随调用方路由既有事务提交）
        await on_prepare_created(
            session,
            project_id=project_id,
            preparation_id=preparation.id,
            actor_id=requested_by,
            status=preparation.status,
            risk_level=preparation.risk_level,
        )
        return preparation, decision

    async def _find_by_idempotency_key(
        self, session: AsyncSession, project_id: uuid.UUID, idempotency_key: str
    ) -> ExecutionPreparation | None:
        """按幂等键查找既有准备单（UK：project_id + idempotency_key，FR-31）。"""
        result = await session.execute(
            select(ExecutionPreparation).where(
                ExecutionPreparation.project_id == project_id,
                ExecutionPreparation.idempotency_key == idempotency_key,
            )
        )
        return result.scalars().first()

    @staticmethod
    def _assert_same_payload(existing: ExecutionPreparation, payload: PrepareInput) -> None:
        """同幂等键不同载荷 → 409（FR-31：幂等键必须绑定确定载荷）。

        比对字段全部取准备单既有列（无需新增 payload_hash 列）。
        """
        if (
            existing.pipeline_version_id != payload.pipeline_version_id
            or existing.environment != payload.environment
            or existing.tool_name != payload.tool_name
            or existing.tool_version != payload.tool_version
        ):
            raise IdempotencyConflict("同一幂等键已用于不同载荷的准备单（FR-31）")

    async def _stored_decision(
        self, session: AsyncSession, preparation: ExecutionPreparation
    ) -> PolicyDecisionResult:
        """重放既有准备单时，从决策行还原裁决结果（不重复请求 PDP）。

        审批槽位取自准备单既有列（`required_roles` / `required_approvals`）——
        决策行只落规则证据，槽位是准备单的治理事实。
        """
        row = await session.get(HarnessPolicyDecision, preparation.policy_decision_id)
        if row is None:
            raise IdempotencyConflict("幂等准备单缺少 PDP 决策记录")
        return PolicyDecisionResult(
            outcome=row.outcome,
            risk_level=row.risk_level,
            rule_evidence=list(row.rule_evidence or []),
            required_roles=list(preparation.required_roles or []),
            required_approvals=preparation.required_approvals or 0,
            policy_package_id=row.policy_package_id or "",
            policy_package_sha256=row.policy_package_sha256 or "",
        )

    async def _build_impact_report(
        self,
        session: AsyncSession,
        *,
        project_id: uuid.UUID,
        version: PipelineVersion,
        digests: dict[str, str],
        payload: PrepareInput,
        decision: PolicyDecisionResult,
    ) -> dict[str, Any]:
        """影响报告（FR-31）：effects / bounded / 资源计数 / 端画像摘要 / 分级 / 监督契约摘要。

        端画像取自 AgentRun 绑定的 Profile 集合（业务库事实），不回传样本明文（AC-02）。
        """
        profile_ids = await self._profile_ids_of_version(session, version.id)
        classifications: list[dict[str, Any]] = []
        aggregate_rows = 0
        if profile_ids:
            result = await session.execute(
                select(DataProfile).where(
                    DataProfile.id.in_(profile_ids),
                )
            )
            for profile in result.scalars().all():
                aggregate_rows += int(
                    (profile.columns or {}).get("approximate_row_count", 0)
                    if isinstance(profile.columns, dict)
                    else 0
                )
                classifications.append(
                    {
                        "endpoint_role": profile.endpoint_role,
                        "table_name": profile.table_name,
                        "classification_report_sha256": profile.classification_report_sha256,
                    }
                )
        contract = dict(DEFAULT_SUPERVISION_CONTRACT)
        return {
            "effects": ["DATA_WRITE", "SHADOW_TABLE_SWAP"],
            "bounded": True,
            "resource_count": len(payload.resource_ids) or 1,
            "source_sink_profiles": classifications,
            "approximate_total_rows": aggregate_rows,
            "classifications_present": bool(payload.classifications),
            "required_roles": decision.required_roles,
            "required_approvals": decision.required_approvals,
            "supervision_contract_sha256": supervision_contract_digest(contract),
            "artifact_digests": digests,
            "project_id": str(project_id),
            # 登记原始入参：Commit 服务端重验指纹的唯一事实来源（不依赖调用方重传，AC-05）
            "requested_input": {
                "side_effect_type": payload.side_effect_type,
                "resource_ids": sorted(str(item) for item in payload.resource_ids),
            },
        }

    @staticmethod
    async def _profile_ids_of_version(
        session: AsyncSession, version_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """取该版本最近一次生成运行绑定的 Profile 集合（FR-16）。"""
        run = (
            (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.pipeline_version_id == version_id)
                    .order_by(AgentRun.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        if run is None:
            return []
        ids: list[uuid.UUID] = []
        for value in [*(run.source_profile_ids or []), *(run.sink_profile_ids or [])]:
            try:
                ids.append(uuid.UUID(str(value)))
            except ValueError:
                continue
        return ids

    @staticmethod
    def _build_rollback_plan(
        *, project_id: uuid.UUID, payload: PrepareInput
    ) -> dict[str, Any]:
        """回滚计划（FR-31/FR-48）：影子表原子发布 → 回滚制品保留期 7 天。"""
        return {
            "strategy": "SHADOW_TABLE_SWAP",
            "artifact_retention_days": 7,
            "capacity_warn_threshold": 20,
            "resource_ids": [str(item) for item in payload.resource_ids],
            "project_id": str(project_id),
        }
