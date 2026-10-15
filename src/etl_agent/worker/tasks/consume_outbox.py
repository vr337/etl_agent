"""M-J/M-K 执行 Worker — Outbox 消费任务（AC-06 核心：验签 → 消费令牌 → 物化 → 提交引擎）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J/M-K / 第 7.1 章主流程时序图 /
第 8 章「高可用 · Outbox 扫描 2s」/ ADR-06
覆盖 FR：FR-35（事务 Outbox 扫描与重试）、FR-33/34（Worker 侧验签 + Replay Guard）、
FR-38（物化 Secret + SeaTunnel 提交 + 指标回传）、FR-43/44（影子表 + 质量分流）、FR-42（稳定 JobId）
对应 AC：AC-06（Worker 调度 SeaTunnel + 影子表 + 质量分流全链路）

单次扫描（`scan_outbox_once`）的四步（FR-35 / ADR-06 至少一次投递）：
1. **回收**：锁过期的 SENDING（Worker 崩溃孤儿）与退避到点的 FAILED → PENDING；
2. **领取**：`claim_next` 条件领取（多副本下同一命令只被一个 Worker 领走）；
3. **执行**：拉取 Outbox → `CapabilityVerifier` 验签 → `ReplayGuard.consume` 消费令牌 →
   物化 Secret（内存短暂持有）→ SeaTunnel 提交 → ExecutionRun QUEUED→SUBMITTED→RUNNING；
4. **推进**：轮询 RUNNING 运行，回传聚合指标，终态落 QualityReport → SUCCEEDED/FAILED。

红线（提示词 §8.2 / 禁止事项 14/15）：
- 凭证明文只在内存，不落库 / 不进日志 / 不回响应（AC-02）；
- 令牌本体只经交接通道（Redis 单次读）到达本任务，不落业务库；
- 绕过 Replay Guard 直接执行 = 越权（FR-34）；
- S7 不做原子 Swap / 监督硬中断（S8）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.config import settings as default_settings
from etl_agent.execution import metrics as metrics_module
from etl_agent.execution.errors import (
    SeatunnelCheckFailed,
    SeatunnelUnavailable,
)
from etl_agent.execution.metrics import ExecutionMetrics
from etl_agent.execution.retry import stable_job_id
from etl_agent.execution.routing import (
    QualityOutcome,
    generate_quality_report,
    route_outcome,
)
from etl_agent.execution.service import (
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUBMITTED,
    STATUS_SUCCEEDED,
    is_terminal,
    load_run,
    record_event,
    transition,
)
from etl_agent.execution.shadow import (
    LoggingShadowTableExecutor,
    ShadowTableExecutor,
    shadow_table_name,
)
from etl_agent.generation.service import load_candidate_content
from etl_agent.harness.broker.replay_guard import ReplayGuard
from etl_agent.harness.capability.verifier import CapabilityVerifier
from etl_agent.harness.lease import LeaseManager
from etl_agent.harness.outbox.service import (
    claim_next,
    mark_failed,
    mark_sent,
    refresh_lock,
    requeue_failed,
    requeue_stale_sending,
)
from etl_agent.harness.side_effects.registry import MAIN_EXECUTION_COMMAND
from etl_agent.infrastructure.models import (
    DataConnection,
    ExecutionEvent,
    ExecutionRun,
    HarnessCapabilityGrant,
    HarnessCommandOutbox,
    PipelineVersion,
)
from etl_agent.secrets.factory import create_provider
from etl_agent.worker.capability_handoff import CapabilityHandoff
from etl_agent.worker.celery_app import app
from etl_agent.worker.credential_materialization import (
    clear_values,
    load_placeholder_contract,
    render_hocon,
    values_for_connection,
)
from etl_agent.worker.errors import classify_failure
from etl_agent.worker.seatunnel_client import (
    ENGINE_TERMINAL_STATUSES,
    SeaTunnelClient,
)

logger = logging.getLogger("etl_agent.worker.consume_outbox")

# 单遍扫描的处理上限（Beat 2s 节奏下保持单次任务短小，剩余下一遍继续）
MAX_CLAIMS_PER_SCAN = 10
MAX_ADVANCE_PER_SCAN = 10

# 默认候选正文加载器（Checkpoint 双库隔离：正文只在 etl_checkpoint，FR-67）
CandidateLoader = Callable[[uuid.UUID], Awaitable[dict[str, object] | None]]


@dataclass
class ScanSummary:
    """单遍扫描结果（供任务返回值与 /health 摘要观测）。"""

    requeued: int = 0
    claimed: int = 0
    submitted: int = 0
    advanced: int = 0
    finalized: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)


async def scan_outbox_once(
    session: AsyncSession,
    *,
    handoff: CapabilityHandoff,
    engine: SeaTunnelClient,
    settings: Settings | None = None,
    candidate_loader: CandidateLoader | None = None,
    shadow_executor: ShadowTableExecutor | None = None,
    worker_id: str | None = None,
    now: datetime | None = None,
) -> ScanSummary:
    """执行一遍 Outbox 扫描（FR-35：回收 → 领取 → 执行 → 推进）。

    Args:
        session: Worker 会话（每个命令处理完显式 commit，进度不因单命令失败丢失）。
        handoff: Capability 令牌交接通道（Worker 通道）。
        engine: SeaTunnel 客户端（Fake / Rest 由部署与测试注入）。
        settings: 应用配置；缺省全局配置。
        candidate_loader: 候选正文加载器（Checkpoint）；缺省走 generation 域加载。
        shadow_executor: 影子表执行通道；缺省 Logging（无 Doris 的开发/测试环境）。
        worker_id: 执行租约持有者标识；缺省主机-PID。
        now: 时钟注入（测试）。

    Returns:
        ScanSummary: 本遍处理计数（不包含任何凭据 / HOCON 正文，AC-02）。
    """
    effective = settings or default_settings
    moment = now or datetime.now(UTC)
    summary = ScanSummary()

    # 1. 回收：崩溃孤儿（SENDING 锁过期）与退避到点的 FAILED（FR-35 重试语义）
    stale = await requeue_stale_sending(session, now=moment)
    retry_ready = await requeue_failed(
        session,
        now=moment,
        max_retries=effective.outbox_max_retries,
        backoff_base_seconds=effective.outbox_retry_backoff_seconds,
    )
    summary.requeued = len(stale) + len(retry_ready)
    await session.commit()

    # 2~3. 领取并逐条执行（单遍上限内循环；剩余命令下一遍继续）
    for _ in range(MAX_CLAIMS_PER_SCAN):
        command = await claim_next(session, now=moment)
        if command is None:
            break
        summary.claimed += 1
        outcome = await _consume_command(
            session,
            command,
            handoff=handoff,
            engine=engine,
            settings=effective,
            candidate_loader=candidate_loader,
            shadow_executor=shadow_executor,
            worker_id=worker_id,
        )
        await session.commit()
        if outcome == "submitted":
            summary.submitted += 1
        else:
            summary.failed += 1
            summary.details.append(f"{command.command_type}: {outcome}")

    # 4. 推进 RUNNING 运行（回传指标 / 终态落 QualityReport）
    finalized = await advance_running_executions(
        session,
        engine,
        settings=effective,
        candidate_loader=candidate_loader,
        worker_id=worker_id,
    )
    summary.advanced = finalized.advanced
    summary.finalized = finalized.finalized
    summary.failed += finalized.failed
    summary.details.extend(finalized.details)
    await session.commit()
    return summary


async def _consume_command(
    session: AsyncSession,
    command: HarnessCommandOutbox,
    *,
    handoff: CapabilityHandoff,
    engine: SeaTunnelClient,
    settings: Settings,
    candidate_loader: CandidateLoader | None,
    shadow_executor: ShadowTableExecutor | None,
    worker_id: str | None,
) -> str:
    """消费一条 Outbox 命令（AC-06 全链路；返回 "submitted" 或失败原因摘要）。

    链路（FR-33/34/38，顺序即安全边界）：
    1. 命令类型必须为主执行命令（9 类副作用留 S8，未实现即显式失败而非静默吞掉）；
    2. 按 `token_id` 取 CapabilityGrant（grant.run_id 必须与本命令的 run 一致）；
    3. 交接通道原子取出令牌本体（GETDEL 单次读，缺失即失败——不降级裸奔）；
    4. `CapabilityVerifier.verify_against` 验签 + claims 绑定逐字段比对；
    5. `ReplayGuard.consume` 原子消费令牌（二次交付 → HARNESS_409_CAPABILITY_REPLAYED）；
    6. 执行租约（FR-37 防并发重入）；
    7. 服务端重验制品绑定（版本当前摘要 vs claims 登记值，防制品被换）；
    8. 物化 Secret（内存短暂持有）→ 影子表就绪 → `--check` → 提交引擎；
    9. QUEUED→SUBMITTED→RUNNING，写稳定 JobId，标记 Outbox 已发送。
    """
    payload = command.payload or {}
    try:
        preparation_id = uuid.UUID(str(payload["preparation_id"]))
        run_id = uuid.UUID(str(payload["run_id"]))
        token_id = uuid.UUID(str(payload["token_id"]))
    except (KeyError, ValueError, TypeError):
        await mark_failed(session, command.id, "outbox 载荷缺字段 / 形态非法")
        return "payload-malformed"

    if command.command_type != MAIN_EXECUTION_COMMAND:
        # 9 类副作用的真实执行在 S8（API-13 回滚 / CANCEL_EXECUTION 取消链路）；
        # S7 显式失败而非静默跳过——静默会让授权过的副作用凭空消失（FR-37 同协议）。
        await mark_failed(
            session, command.id, f"副作用命令 {command.command_type} 不属于 S7 执行范围"
        )
        return "unsupported-command"

    try:
        grant = await _load_grant(session, token_id)
        if grant is None or grant.run_id != run_id:
            await _fail_command(session, command, run_id, "capability grant 缺失或与本运行不匹配")
            return "grant-mismatch"

        token = await handoff.fetch(token_id)
        if not token:
            # 令牌交接丢失（Redis TTL 到期 / 已被取走）：必须失败并可重试，
            # 由运维重新 prepare→approve→commit（FR-33 单次令牌不可补发）。
            await _fail_command(
                session,
                command,
                run_id,
                "capability 交接通道无令牌（可能已过期或被取走）",
                retryable=True,
            )
            return "handoff-missing"

        verifier = CapabilityVerifier(settings.harness_public_key_list)
        claims = verifier.verify_against(token, now=datetime.now(UTC), expected=dict(grant.claims or {}))

        await ReplayGuard().consume(
            session, token_id=token_id, token_sha256=grant.token_sha256
        )

        lease = LeaseManager()
        lease_key = LeaseManager.build_key(preparation_id, MAIN_EXECUTION_COMMAND)
        owner = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        await lease.acquire(lease_key, owner=owner, ttl_seconds=settings.outbox_lock_seconds * 4)

        try:
            run = await load_run(session, run_id)
            if is_terminal(run.status):
                # 迟到的重复交付且运行已终态：不二次执行，直接销账（幂等，FR-35）
                await mark_sent(session, command.id)
                return "submitted"
            if run.status == STATUS_QUEUED:
                await transition(
                    session, run, STATUS_SUBMITTED, reason="Worker 已领取受管执行命令"
                )
                await session.flush()

            version = await session.get(PipelineVersion, run.pipeline_version_id)
            if version is None or version.artifact_sha256 != claims.get("artifact_sha256"):
                await _fail_command(
                    session,
                    command,
                    run_id,
                    "制品绑定漂移：版本当前摘要与令牌 claims 不一致（FR-33）",
                    retryable=False,
                )
                return "artifact-drift"

            candidate = await _load_candidate(candidate_loader, session, run.pipeline_version_id, settings)
            hocon = candidate.get("hocon") if candidate else None
            plan = candidate.get("etl_plan") if candidate else None
            if not hocon or not isinstance(hocon, str):
                await _fail_command(
                    session, command, run_id, "候选正文不可用（Checkpoint 无 HOCON，FR-67）"
                )
                return "candidate-missing"

            materialized = await _materialize(
                session,
                plan if isinstance(plan, dict) else {},
                hocon,
                settings,
            )
            shadow = await _ensure_shadow(
                shadow_executor, plan if isinstance(plan, dict) else {}
            )
            quality_contract = (
                plan.get("quality_contract") if isinstance(plan, dict) else None
            )
            if isinstance(quality_contract, dict) and _shadow_contract_mismatch(
                quality_contract, shadow
            ):
                # 契约 valid_sink 与计划推得的影子表漂移：可观测事实，不静默忽略
                await record_event(
                    session,
                    run.id,
                    level="WARN",
                    event_type="execution.shadow_contract_drift",
                    message="质量契约 valid_sink 与计划推得的影子表不一致（以计划为准）",
                    stage="submit",
                )

            check = await engine.check(materialized)
            if not check.get("ok"):
                raise SeatunnelCheckFailed(
                    f"引擎 --check 未通过：{check.get('summary', 'unknown')}"
                )

            job_id = await engine.submit_job(
                materialized, job_name=stable_job_id(run.pipeline_version_id)
            )
            run = await load_run(session, run_id)
            run.seatunnel_job_id = job_id or stable_job_id(run.pipeline_version_id)
            await transition(session, run, STATUS_RUNNING, reason=f"引擎作业已提交：{run.seatunnel_job_id}")
            await record_event(
                session,
                run.id,
                level="INFO",
                event_type="execution.shadow_ready",
                message=f"影子表就绪：{shadow}（S7 不 Swap，FR-43）",
                stage="submit",
            )
            await refresh_lock(session, command.id, lock_seconds=settings.outbox_lock_seconds * 4)
            await mark_sent(session, command.id)
            return "submitted"
        finally:
            await lease.release(lease_key, owner=owner)
    except Exception as exc:  # noqa: BLE001 -- 统一分类落账，绝不把异常抛回 Beat
        category, fail_stage, retryable = classify_failure(exc)
        await _fail_command(
            session,
            command,
            run_id,
            f"{type(exc).__name__}: {_brief(str(exc))}",
            failure_category=category,
            failure_stage=fail_stage,
            retryable=retryable,
        )
        return f"failed:{type(exc).__name__}"


@dataclass
class _AdvanceResult:
    """RUNNING 推进结果。"""

    advanced: int = 0
    finalized: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)


async def _load_grant(
    session: AsyncSession, token_id: uuid.UUID
) -> HarnessCapabilityGrant | None:
    """按 token_id 取令牌登记行（只含 token_sha256 与 claims，无令牌本体，FR-33）。"""
    return (
        (
            await session.execute(
                select(HarnessCapabilityGrant).where(
                    HarnessCapabilityGrant.token_id == token_id
                )
            )
        )
        .scalars()
        .first()
    )


async def _fail_command(
    session: AsyncSession,
    command: HarnessCommandOutbox,
    run_id: uuid.UUID,
    reason: str,
    *,
    failure_category: str | None = None,
    failure_stage: str | None = None,
    retryable: bool = True,
) -> None:
    """命令失败落账：运行置 FAILED（若非终态）+ Outbox 置 FAILED（退避重试由扫描器回收）。

    口径：失败必须同时反映在**运行**与**命令**两处——只落命令会让运行中心看到
    「永远 QUEUED」的假活运行；只落运行会让 Outbox 永远停在 SENDING 被反复领取。
    """
    summary = _brief(reason)
    run = await session.get(ExecutionRun, run_id)
    if run is not None and not is_terminal(run.status):
        await transition(
            session,
            run,
            STATUS_FAILED,
            reason=summary,
            failure_category=failure_category,
            failure_stage=failure_stage,
            retryable=retryable,
            diagnosis=summary,
        )
    await mark_failed(session, command.id, summary)


async def _load_candidate(
    loader: CandidateLoader | None,
    session: AsyncSession,
    version_id: uuid.UUID,
    settings: Settings,
) -> dict[str, object] | None:
    """加载版本候选正文（`{"etl_plan": ..., "hocon": ...}`；FR-67 只在 Checkpoint）。"""
    if loader is not None:
        return await loader(version_id)
    return await load_candidate_content(session, version_id, settings)


async def _load_connection(
    session: AsyncSession, raw_connection_id: object
) -> DataConnection | None:
    """按 ETL 计划登记的 connection_id 取连接行（非 UUID 形态返回 None，由物化侧判定缺失）。"""
    try:
        connection_id = uuid.UUID(str(raw_connection_id))
    except (ValueError, TypeError):
        return None
    return await session.get(DataConnection, connection_id)


async def _materialize(
    session: AsyncSession,
    plan: dict[str, object],
    hocon: str,
    settings: Settings,
) -> str:
    """物化 Secret：占位符 → 真实值（内存短暂持有，AC-02 红线）。

    取值来源：连接行非密字段（host / port / database_name）+ SecretProvider.read（用户名 / 口令）。
    返回的 HOCON 含明文，**只在提交引擎前短暂持有**，调用方用完即弃引用（不落库 / 不进日志）。
    """
    contract = load_placeholder_contract()
    values: dict[str, str] = {}
    provider = create_provider(session, settings)
    for role, prefix in (("source", "SRC"), ("sink", "SINK")):
        endpoint = plan.get(role)
        if not isinstance(endpoint, dict):
            continue
        connection = await _load_connection(session, endpoint.get("connection_id"))
        if connection is None:
            continue
        secret_values: dict[str, str] = {}
        if connection.secret_ref is not None:
            secret_values = await provider.read(connection.secret_ref)
        values.update(values_for_connection(connection, secret_values, prefix))
        clear_values(secret_values)  # 明文只在 values 中保留一份引用（AC-02）
    if _is_file_source(plan.get("source")):
        values["FILE_ASSET_BASE"] = settings.file_asset_root
    try:
        return render_hocon(hocon, values, contract)
    finally:
        clear_values(values)


def _is_file_source(endpoint: object) -> bool:
    """来源端是否为受管文件资产（LOCAL_FILE 连接 / FILE 连接器）。"""
    if not isinstance(endpoint, dict):
        return False
    connector = str(endpoint.get("connector_type") or "").upper()
    return connector in {"LOCAL_FILE", "FILE"}


async def _ensure_shadow(
    executor: ShadowTableExecutor | None, plan: dict[str, object]
) -> str:
    """影子表预复制（S7 只创建，不 Swap，FR-43）。

    目标表取自 ETL 计划的 `sink.table`；契约声明的 `valid_sink` 与 `<target>_shadow`
    不一致时以计划为准并记入事件流（质量契约与计划漂移属可观测事实，不是静默忽略）。
    计划未声明目标表（无落库动作）时不做预复制，返回空串。
    """
    sink = plan.get("sink")
    target = sink.get("table") if isinstance(sink, dict) else None
    if not target:
        return ""
    effective = executor or LoggingShadowTableExecutor()
    return await effective.ensure_shadow_table(str(target))


def _quality_outcome(
    metrics_payload: dict[str, object], aggregated: ExecutionMetrics
) -> QualityOutcome:
    """从引擎回传载荷构造质量分流事实（无 quality 段时按源/目标行数降级推断）。"""
    quality = metrics_payload.get("quality")
    if isinstance(quality, dict):
        error_codes = quality.get("error_codes")
        return QualityOutcome(
            total_rows=_as_optional_int(quality.get("total_rows")),
            passed_rows=_as_optional_int(quality.get("passed_rows")) or 0,
            rejected_rows=_as_optional_int(quality.get("rejected_rows")) or 0,
            error_codes={
                str(key): int(value)
                for key, value in (error_codes or {}).items()
                if isinstance(value, int)
            }
            if isinstance(error_codes, dict)
            else {},
        )
    total = aggregated.source_rows
    passed = aggregated.sink_rows
    rejected = None
    if total is not None and passed is not None:
        rejected = max(total - passed, 0)
    return QualityOutcome(
        total_rows=total,
        passed_rows=passed or 0,
        rejected_rows=rejected or 0,
    )


def _as_optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _brief(text: str, limit: int = 300) -> str:
    """摘要截断（事件流 / 失败原因只落治理事实，不含 HOCON 正文与凭据，AC-02）。"""
    return " ".join(text.split())[:limit]


async def advance_running_executions(
    session: AsyncSession,
    engine: SeaTunnelClient,
    *,
    settings: Settings,
    candidate_loader: CandidateLoader | None = None,
    worker_id: str | None = None,
    now: datetime | None = None,
) -> _AdvanceResult:
    """推进 RUNNING / CANCELLING 运行（FR-39：指标回传 + 终态落质量报告）。

    单遍对每个非终态运行查询一次引擎状态——**跨遍推进而非单遍长轮询**：Beat 节奏
    2s 天然构成轮询间隔，任务保持短小（长轮询会把一个 2s 任务拖成分钟级，
    单副本 Beat 下直接阻塞后续周期）。`execution_poll_max_wait_seconds` 用作
    **停滞阈值**：超过阈值仍无终态 → 记一条 WARN 事件（不硬中断，硬中断属 S8 监督链路）。
    """
    moment = now or datetime.now(UTC)
    result = _AdvanceResult()
    runs = (
        (
            await session.execute(
                select(ExecutionRun)
                .where(ExecutionRun.status.in_([STATUS_RUNNING, STATUS_CANCELLING]))
                .order_by(ExecutionRun.started_at)
                .limit(MAX_ADVANCE_PER_SCAN)
            )
        )
        .scalars()
        .all()
    )
    for run in runs:
        if not run.seatunnel_job_id:
            continue
        try:
            observation = await engine.job_status(run.seatunnel_job_id)
        except SeatunnelUnavailable as exc:
            result.failed += 1
            result.details.append(f"run {run.id}: 引擎不可达（{_brief(str(exc))}）")
            continue
        run.engine_status = observation.status
        metrics_payload = observation.metrics if isinstance(observation.metrics, dict) else {}
        aggregated = metrics_module.metrics_from_engine_payload(metrics_payload)
        if observation.status in ENGINE_TERMINAL_STATUSES:
            await _finalize_run(
                session,
                run,
                observation.status,
                observation.error,
                metrics_payload,
                aggregated,
                settings=settings,
                candidate_loader=candidate_loader,
            )
            result.finalized += 1
            continue
        await metrics_module.apply_metrics(session, run, aggregated)
        result.advanced += 1
        await _warn_if_stalled(session, run, moment, settings)
    await session.flush()
    return result


async def _finalize_run(
    session: AsyncSession,
    run: ExecutionRun,
    engine_status: str,
    engine_error: str | None,
    metrics_payload: dict[str, object],
    aggregated: ExecutionMetrics,
    *,
    settings: Settings,
    candidate_loader: CandidateLoader | None,
) -> None:
    """终态落账：指标 + QualityReport + 状态迁移（FR-39/44）。"""
    await metrics_module.apply_metrics(session, run, aggregated)
    run.seatunnel_job_id = run.seatunnel_job_id or stable_job_id(run.pipeline_version_id)
    if engine_status == "FINISHED":
        contract = await _quality_contract(session, run, settings, candidate_loader)
        outcome = route_outcome(contract, _quality_outcome(metrics_payload, aggregated))
        report = await generate_quality_report(session, run.id, contract, outcome)
        await record_event(
            session,
            run.id,
            level="INFO" if report.status in {"PASSED", "WARNING"} else "WARN",
            event_type="execution.quality_report",
            message=(
                f"质量报告 {report.status}：总 {report.total_rows} / 通过 {report.passed_rows}"
                f" / 拒绝 {report.rejected_rows}（S7 不 Swap，FR-43）"
            ),
            stage="finalize",
        )
        await transition(
            session,
            run,
            STATUS_SUCCEEDED,
            reason=f"引擎作业完成：{engine_status}",
        )
        return
    if engine_status == "CANCELED":
        target = STATUS_CANCELLED
        reason = "引擎侧作业已取消"
    else:
        target = STATUS_FAILED
        reason = f"引擎作业失败：{_brief(engine_error or engine_status)}"
    await transition(
        session,
        run,
        target,
        reason=reason,
        failure_category="ENGINE" if target == STATUS_FAILED else None,
        failure_stage="run" if target == STATUS_FAILED else None,
        retryable=True if target == STATUS_FAILED else None,
        diagnosis=_brief(engine_error) if engine_error else None,
    )


async def _quality_contract(
    session: AsyncSession,
    run: ExecutionRun,
    settings: Settings,
    candidate_loader: CandidateLoader | None,
) -> dict[str, object]:
    """取本次运行的质量契约（来自冻结计划的 `quality_contract`，缺省空契约）。

    契约只在 Checkpoint 候选正文里——S7 不做契约副本落库（避免业务库出现第二份事实源）。
    取不到时返回空契约：报告仍生成，按引擎回传事实判定（`classify_quality` 对空契约
    的语义是「无阈值约束」，不臆造阈值）。
    """
    candidate = await _load_candidate(candidate_loader, session, run.pipeline_version_id, settings)
    plan = candidate.get("etl_plan") if candidate else None
    if isinstance(plan, dict):
        contract = plan.get("quality_contract")
        if isinstance(contract, dict):
            return contract
    return {}


async def _warn_if_stalled(
    session: AsyncSession,
    run: ExecutionRun,
    moment: datetime,
    settings: Settings,
) -> None:
    """停滞告警（超过 `execution_poll_max_wait_seconds` 仍非终态；同一停滞期只记一次）。"""
    started = run.started_at
    if started is None:
        return
    elapsed = (moment - started).total_seconds()
    if elapsed <= settings.execution_poll_max_wait_seconds:
        return
    last = (
        (
            await session.execute(
                select(ExecutionEvent)
                .where(ExecutionEvent.run_id == run.id)
                .order_by(ExecutionEvent.occurred_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if last is not None and last.event_type == "execution.stalled":
        return
    await record_event(
        session,
        run.id,
        level="WARN",
        event_type="execution.stalled",
        message=(
            f"执行推进停滞：已运行 {int(elapsed)}s 未达终态"
            f"（阈值 {settings.execution_poll_max_wait_seconds}s，硬中断属 S8 监督链路）"
        ),
        stage="run",
    )


def _shadow_contract_mismatch(contract: dict[str, object], shadow: str) -> bool:
    """契约声明的 valid_sink 与计划推得的影子表是否一致（漂移即事件流留痕）。"""
    valid_sink = contract.get("valid_sink")
    return bool(valid_sink) and shadow_table_name(str(valid_sink)) != shadow


async def _run() -> dict[str, object]:
    """Celery 任务体：开会话 → 装配交接通道 / 引擎 / 影子表通道 → 扫描一遍。"""
    from etl_agent.infrastructure.redis_client import create_redis_client
    from etl_agent.worker.capability_handoff import RedisCapabilityHandoff
    from etl_agent.worker.seatunnel_client import RestSeaTunnelClient
    from etl_agent.worker.session import worker_session

    settings = default_settings
    redis = create_redis_client(settings.resolved_redis_url)
    handoff = RedisCapabilityHandoff(redis, key_prefix=settings.capability_handoff_key_prefix)
    engine = RestSeaTunnelClient(
        settings.seatunnel_url,
        submit_timeout_seconds=settings.seatunnel_submit_timeout_seconds,
        check_timeout_seconds=settings.seatunnel_check_timeout_seconds,
    )
    try:
        async with worker_session() as session:
            summary = await scan_outbox_once(
                session,
                handoff=handoff,
                engine=engine,
                settings=settings,
            )
    finally:
        await redis.aclose()
    return {
        "requeued": summary.requeued,
        "claimed": summary.claimed,
        "submitted": summary.submitted,
        "advanced": summary.advanced,
        "finalized": summary.finalized,
        "failed": summary.failed,
    }


@app.task(name="worker.scan_outbox", bind=True, acks_late=True)
def scan_outbox(self: object) -> dict[str, object]:
    """Celery 入口（Beat 2s；薄壳：装配依赖 → 扫描一遍 → 返回计数摘要）。"""
    return asyncio.run(_run())


__all__ = [
    "MAX_ADVANCE_PER_SCAN",
    "MAX_CLAIMS_PER_SCAN",
    "ScanSummary",
    "advance_running_executions",
    "scan_outbox",
    "scan_outbox_once",
]

