"""M-F 生成引擎 — AgentRun 生命周期服务。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 5.2 章 agent_runs / 第 6.1 章 API-05/06/07 / 第 5.3 章状态隔离
覆盖 FR：FR-16（运行生命周期 QUEUED/RUNNING/WAITING_INPUT/WAITING_APPROVAL/FAILED）、FR-17（同 thread 恢复）、FR-67/68（正文不落业务库）
对应 AC：AC-03（中断与恢复）、AC-13（三层状态隔离）

三条红线：
1. **业务库不存正文**：`requirement` 只进图状态（落 `etl_checkpoint`），
   `agent_runs` 只写状态 / 挂起问题 / 6 个非敏感遥测字段（FR-67）；
2. **跨项目视同不存在**：运行 / 版本 / 画像查询一律以 `project_id` 过滤（FR-66）；
3. **失败也要留痕**：图内异常与预算耗尽都会把运行置为 FAILED 并落遥测，再对外报错。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import AppError, NotFound, StateConflict
from etl_agent.config import Settings
from etl_agent.files.errors import FileAssetNotFound
from etl_agent.gate.engine_check import LocalStubEngineChecker
from etl_agent.generation.checkpoint import open_checkpointer
from etl_agent.generation.errors import AgentRunNotFound, InterruptNotPending
from etl_agent.generation.graph import build_graph
from etl_agent.generation.schemas import AgentRunCreateRequest
from etl_agent.generation.state import (
    FAILURE_BUDGET_EXHAUSTED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_WAITING_INPUT,
    GenerationDeps,
    GenerationState,
)
from etl_agent.generation.telemetry import GenerationTelemetry
from etl_agent.infrastructure.models import (
    AgentRun,
    DataConnection,
    DataProfile,
    FileAsset,
    Pipeline,
    PipelineVersion,
)
from etl_agent.llm.errors import LLMBudgetExhausted
from etl_agent.llm.factory import create_provider
from etl_agent.llm.provider import LLMProvider
from etl_agent.profiles.errors import ProfileNotFound

# 允许在其上发起生成的版本状态（APPROVED 等终态必须派生新版本，FR-26）
GENERATABLE_STATUSES = frozenset({"DRAFT", "PROFILING", "GENERATED"})


async def create_run(
    session: AsyncSession,
    project_id: uuid.UUID,
    payload: AgentRunCreateRequest,
    settings: Settings,
    *,
    provider: LLMProvider | None = None,
    checkpointer: Any = None,
) -> AgentRun:
    """创建生成运行并推进状态机直到中断 / 完成（API-05 / FR-16）。

    参数缺失时状态转 `WAITING_INPUT` 并挂起澄清问题（AC-03）——因此返回值是
    **推进后的真实状态**，而非固定 `QUEUED`。

    Args:
        session: 请求级数据库会话。
        project_id: 隔离边界。
        payload: 创建请求（需求 / Profile 集合 / 可选版本）。
        settings: 应用配置。
        provider: Provider 覆盖（测试注入）；缺省按配置构造。
        checkpointer: Checkpointer 覆盖（测试注入 InMemorySaver）；缺省连 etl_checkpoint。

    Returns:
        AgentRun: 落库后的运行（含状态、挂起问题、遥测）。

    Raises:
        NotFound:          版本 / 画像不存在或跨项目。
        StateConflict:     版本状态不允许生成。
        LLMProviderError:  Provider 调用失败。
        LLMBudgetExhausted: 候选预算耗尽（运行已置 FAILED）。
    """
    version = await _resolve_version(session, project_id, payload.pipeline_version_id)
    await _validate_references(session, project_id, payload)

    run = AgentRun(
        pipeline_version_id=version.id,
        thread_id=str(uuid.uuid4()),
        status=STATUS_QUEUED,
        source_profile_ids=[str(item) for item in payload.source_profile_ids],
        sink_profile_ids=[str(item) for item in payload.sink_profile_ids],
        file_asset_ids=[str(item) for item in payload.file_asset_ids],
    )
    session.add(run)
    await session.flush()

    initial: GenerationState = {
        "run_id": str(run.id),
        "project_id": str(project_id),
        "pipeline_version_id": str(version.id),
        # requirement 只进图状态 → 落 etl_checkpoint，绝不写入业务库（FR-67）
        "requirement": payload.requirement,
        "source_profile_ids": [str(item) for item in payload.source_profile_ids],
        "sink_profile_ids": [str(item) for item in payload.sink_profile_ids],
        "file_asset_ids": [str(item) for item in payload.file_asset_ids],
        "candidate_count": 0,
        "repair_count": 0,
        "status": STATUS_QUEUED,
    }
    await _advance(session, run, settings, provider, checkpointer, initial=initial)
    return run


async def resume_run(
    session: AsyncSession,
    run: AgentRun,
    answer: str,
    settings: Settings,
    *,
    provider: LLMProvider | None = None,
    checkpointer: Any = None,
) -> AgentRun:
    """澄清回答并恢复运行（API-06 / FR-17 / AC-03）。

    Args:
        session: 请求级数据库会话。
        run: 已加载的运行（调用方已完成项目成员校验）。
        answer: 用户回答（只进图状态，不落业务库）。
        settings: 应用配置。
        provider: Provider 覆盖（测试注入）。
        checkpointer: Checkpointer 覆盖（测试注入）。

    Returns:
        AgentRun: 恢复推进后的运行。

    Raises:
        InterruptNotPending: 运行不在 WAITING_INPUT（非中断态收到回答 → 422）。
    """
    if run.status != STATUS_WAITING_INPUT:
        raise InterruptNotPending(f"运行当前状态为 {run.status}，不存在待恢复的澄清中断")
    await _advance(session, run, settings, provider, checkpointer, resume=answer)
    return run


async def load_run(session: AsyncSession, run_id: uuid.UUID) -> tuple[AgentRun, uuid.UUID]:
    """加载运行及其所属项目（API-06/07 无 project_id 路径参数，需回溯项目做鉴权）。

    Raises:
        AgentRunNotFound: 运行不存在（跨项目由调用方的成员校验拒绝）。
    """
    result = await session.execute(
        select(AgentRun, Pipeline.project_id)
        .join(PipelineVersion, AgentRun.pipeline_version_id == PipelineVersion.id)
        .join(Pipeline, PipelineVersion.pipeline_id == Pipeline.id)
        .where(AgentRun.id == run_id)
    )
    row = result.one_or_none()
    if row is None:
        raise AgentRunNotFound("生成运行不存在")
    return row[0], row[1]


async def _advance(
    session: AsyncSession,
    run: AgentRun,
    settings: Settings,
    provider: LLMProvider | None,
    checkpointer: Any,
    *,
    initial: GenerationState | None = None,
    resume: str | None = None,
) -> None:
    """推进状态机并把结果落到 AgentRun（含异常路径的 FAILED 留痕）。"""
    started = time.perf_counter()
    deps = GenerationDeps(
        settings=settings,
        provider=provider or create_provider(settings),
        session=session,
        engine_checker=LocalStubEngineChecker(),
    )
    try:
        if checkpointer is None:
            async with open_checkpointer(settings) as saver:
                result = await _invoke(deps, saver, run.thread_id, initial, resume)
        else:
            result = await _invoke(deps, checkpointer, run.thread_id, initial, resume)
    except AppError as exc:
        # 失败也要留痕：图内异常（Provider 失败 / 画像缺失）落 FAILED + 失败分类后原样抛出
        _record_telemetry(
            run,
            deps,
            {},
            int((time.perf_counter() - started) * 1000),
            failure_category=exc.code,
        )
        run.status = STATUS_FAILED
        run.pending_question = None
        await session.flush()
        await session.commit()
        raise

    duration_ms = int((time.perf_counter() - started) * 1000)
    _finalize(run, result, deps, duration_ms)
    await session.flush()
    await session.commit()
    if run.status == STATUS_FAILED:
        raise LLMBudgetExhausted(
            f"{FAILURE_BUDGET_EXHAUSTED}: 候选预算耗尽（上限 {settings.llm_max_candidates} 份，含初始候选）"
        )


async def _invoke(
    deps: GenerationDeps,
    checkpointer: Any,
    thread_id: str,
    initial: GenerationState | None,
    resume: str | None,
) -> dict[str, Any]:
    """调用图：首次用初始状态，恢复用 `Command(resume=...)`（同 thread_id）。"""
    graph = build_graph(deps, checkpointer)
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    payload: Any = Command(resume=resume) if resume is not None else (initial or {})
    result: Any = await graph.ainvoke(payload, config)
    return result if isinstance(result, dict) else {}


def _finalize(
    run: AgentRun, result: dict[str, Any], deps: GenerationDeps, duration_ms: int
) -> None:
    """把图结果写回运行：状态 / 挂起问题 / 遥测（不写任何正文，FR-67）。"""
    _record_telemetry(
        run, deps, result, duration_ms, failure_category=result.get("failure_category")
    )
    interrupts = result.get("__interrupt__")
    if interrupts:
        run.status = STATUS_WAITING_INPUT
        run.pending_question = _pending_question(interrupts)
        return
    run.status = str(result.get("status") or STATUS_FAILED)
    run.pending_question = None


def _record_telemetry(
    run: AgentRun,
    deps: GenerationDeps,
    result: dict[str, Any],
    duration_ms: int,
    *,
    failure_category: str | None = None,
) -> None:
    """写入 6 个非敏感遥测字段（只记计数 / 耗时 / 诊断分类）。"""
    candidate = result.get("candidate") or {}
    telemetry = GenerationTelemetry(
        backend=str(candidate.get("backend") or deps.settings.llm_provider),
        model=str(candidate.get("model") or deps.settings.llm_model or "-"),
        candidate_count=int(result.get("candidate_count") or 0),
        repair_count=int(result.get("repair_count") or 0),
        duration_ms=duration_ms,
        failure_category=failure_category,
    )
    telemetry.apply_to(run)


def _pending_question(interrupts: Any) -> str:
    """取中断值中的静态澄清问题（中断值不含用户输入）。"""
    first = interrupts[0] if isinstance(interrupts, (list, tuple)) and interrupts else None
    value = getattr(first, "value", None)
    if isinstance(value, dict):
        return str(value.get("question") or "")
    return ""


async def _resolve_version(
    session: AsyncSession, project_id: uuid.UUID, version_id: uuid.UUID | None
) -> PipelineVersion:
    """定位目标版本：未提供则自动建 Pipeline + 首个版本（S5 无版本链管理）。

    自动创建的 Pipeline 名不含需求文本——名称落业务库，夹带需求即等于 Prompt 落库（FR-67）。
    """
    if version_id is None:
        pipeline = Pipeline(project_id=project_id, name=f"pipeline-{uuid.uuid4().hex[:8]}")
        session.add(pipeline)
        await session.flush()
        version = PipelineVersion(pipeline_id=pipeline.id, version_number=1, status="DRAFT")
        session.add(version)
        await session.flush()
        return version

    result = await session.execute(
        select(PipelineVersion, Pipeline.project_id)
        .join(Pipeline, PipelineVersion.pipeline_id == Pipeline.id)
        .where(PipelineVersion.id == version_id)
    )
    row = result.one_or_none()
    if row is None or row[1] != project_id:
        raise NotFound("Pipeline 版本不存在或不属于当前项目")
    version = row[0]
    if version.status not in GENERATABLE_STATUSES:
        raise StateConflict(f"版本状态 {version.status} 不允许生成（APPROVED 须派生新版本，FR-26）")
    return version


async def _validate_references(
    session: AsyncSession, project_id: uuid.UUID, payload: AgentRunCreateRequest
) -> None:
    """校验引用的画像 / 文件资产均属当前项目（跨项目视同不存在，FR-66）。

    缺失的集合不在此拦截——那是 AC-03 的澄清中断分支（FR-16/17）。
    """
    await _require_owned(session, DataProfile, DataConnection, payload.source_profile_ids, project_id)
    await _require_owned(session, DataProfile, DataConnection, payload.sink_profile_ids, project_id)
    await _require_owned(session, FileAsset, None, payload.file_asset_ids, project_id)


async def _require_owned(
    session: AsyncSession,
    model: Any,
    via: Any,
    ids: list[uuid.UUID],
    project_id: uuid.UUID,
) -> None:
    """逐集合校验归属（空集合直接放行，交由澄清分支处理）。"""
    if not ids:
        return
    if via is None:
        stmt = select(model.id).where(model.id.in_(ids), model.project_id == project_id)
    else:
        stmt = (
            select(model.id)
            .join(via, model.connection_id == via.id)
            .where(model.id.in_(ids), via.project_id == project_id)
        )
    found = {str(item) for item in (await session.execute(stmt)).scalars().all()}
    if len(found) != len(ids):
        if model is FileAsset:
            raise FileAssetNotFound("文件资产不存在或不属于当前项目")
        raise ProfileNotFound("Profile 不存在或不属于当前项目")
