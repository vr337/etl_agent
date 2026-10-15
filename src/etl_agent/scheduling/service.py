"""M-K/T-14 调度 — 调度策略服务（cron 解析 + CRUD + 到期扫描，FR-40/41）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 5.2 章 pipeline_schedules /
第 6.1 章 API-11 / 第 8 章「高可用 · 调度扫描 30s」
覆盖 FR：FR-40（cron + 时区；BATCH / WATERMARK_INCREMENTAL；misfire SKIP/FIRE_ONCE 宽限 120s；
扫描间隔默认 30s）、FR-41（水印推进与 `watermark_window`）、FR-23（增量契约）
对应 AC：AC-06（调度不绕过 Harness；到期触发走 prepare 通道）

调度扫描的**授权口径**（本阶段最重要的设计裁决）：
调度扫描**不自行授权执行**，只为到期槽位创建 **Harness 准备单**（走与手工触发完全相同的
PDP 评级 + 四眼审批 + Commit 链路）。理由：
- FR-34 / 禁止事项 15 要求所有副作用经 Harness 授权，调度不是例外；
- S7 无「系统自动审批」策略（那属于策略包与人工裁决范畴，禁止事项 7 不许自造契约）；
- 结果：调度保证「到期即有受管请求」，但真正执行仍需 OPERATOR Commit——
  `trigger=SCHEDULED` 的事实由 `schedule_id` / `scheduled_for` 在执行运行时体现。

无状态到期判定（不新增列，禁止事项 7）：`pipeline_schedules` 无「上次触发」列，
故以 `created_at` 为锚点、用 cron 反推**最近的已到期槽位**；同一槽位重复扫描因
幂等键 `sched-<schedule_id>-<槽位时刻>` 命中同一张准备单（FR-31 幂等语义），
既不会堆叠请求，也不需要额外的调度状态表。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.config import settings as default_settings
from etl_agent.harness.prepare.service import PrepareInput, PrepareService
from etl_agent.infrastructure.models import (
    ExecutionPreparation,
    Pipeline,
    PipelineSchedule,
    PipelineVersion,
    ProjectMembership,
)
from etl_agent.scheduling.errors import (
    ScheduleNotFound,
    ScheduleStateConflict,
    ScheduleValidationError,
)
from etl_agent.scheduling.misfire import (
    ALLOWED_MISFIRE_POLICIES,
    DEFAULT_GRACE_SECONDS,
    MisfireDecision,
    decide_misfire,
)
from etl_agent.scheduling.watermark import WatermarkWindow, resolve_window

# 同步模式（SPEC 5.2 pipeline_schedules.mode）
MODE_BATCH = "BATCH"
MODE_WATERMARK_INCREMENTAL = "WATERMARK_INCREMENTAL"
ALLOWED_MODES: frozenset[str] = frozenset({MODE_BATCH, MODE_WATERMARK_INCREMENTAL})

# 默认时区（提示词 §8.7：Celery 时区 Asia/Shanghai）
DEFAULT_TIMEZONE = "Asia/Shanghai"

# 正向 / 反向搜索上限：一年内无匹配即判表达式不可达（避免死循环）
MAX_SEARCH_DAYS = 366

# cron 字段范围：分 / 时 / 日 / 月 / 星期（0 与 7 均表示周日）
_FIELD_RANGES: tuple[tuple[int, int], ...] = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


@dataclass(frozen=True)
class CronExpression:
    """已解析的 5 段 cron 表达式（数值形态，不支持月份 / 星期英文别名）。"""

    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_restricted: bool
    weekday_restricted: bool


@dataclass(frozen=True)
class ScheduleInput:
    """调度策略入参（cron + 时区 + 模式 + misfire + 水印契约）。"""

    cron: str
    timezone: str = DEFAULT_TIMEZONE
    mode: str = MODE_BATCH
    misfire_policy: str = "SKIP"
    misfire_grace_seconds: int = DEFAULT_GRACE_SECONDS
    watermark_column: str | None = None
    watermark_initial: str | None = None
    watermark_lag_seconds: int | None = None


@dataclass
class ScheduleScanSummary:
    """一遍调度扫描的结果（供任务返回值与可观测摘要）。"""

    evaluated: int = 0
    fired: int = 0
    skipped: int = 0
    deferred: int = 0
    details: list[str] = field(default_factory=list)


def _parse_field(text: str, low: int, high: int) -> tuple[frozenset[int], bool]:
    """解析单段 cron 字段，返回 (取值集合, 是否受限)。

    支持 `*` / `n` / `a-b` / `a,b,c` / `*/n` / `a-b/n`；越界与倒序区间一律拒绝。
    """
    restricted = text.strip() != "*"
    values: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ScheduleValidationError(f"cron 字段存在空项：{text!r}")
        step = 1
        body = part
        if "/" in part:
            body, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) <= 0:
                raise ScheduleValidationError(f"cron 步长非法：{part!r}")
            step = int(step_text)
        if body == "*":
            start, end = low, high
        elif "-" in body:
            start_text, _, end_text = body.partition("-")
            if not start_text.isdigit() or not end_text.isdigit():
                raise ScheduleValidationError(f"cron 区间非法：{part!r}")
            start, end = int(start_text), int(end_text)
        elif body.isdigit():
            start = end = int(body)
        else:
            raise ScheduleValidationError(f"cron 取值非法：{part!r}（仅支持数值形态）")
        if start < low or end > high or start > end:
            raise ScheduleValidationError(
                f"cron 取值越界：{part!r}（合法区间 {low}-{high}）"
            )
        values.update(range(start, end + 1, step))
    return frozenset(values), restricted


def parse_cron(expression: str) -> CronExpression:
    """解析 5 段 cron 表达式（分 时 日 月 星期）。

    Raises:
        ScheduleValidationError: 段数不符 / 取值非法 / 越界。
    """
    fields = expression.split()
    if len(fields) != 5:
        raise ScheduleValidationError(
            f"cron 表达式必须为 5 段（分 时 日 月 星期），当前 {len(fields)} 段：{expression!r}"
        )
    minutes, _ = _parse_field(fields[0], *_FIELD_RANGES[0])
    hours, _ = _parse_field(fields[1], *_FIELD_RANGES[1])
    days, day_restricted = _parse_field(fields[2], *_FIELD_RANGES[2])
    months, _ = _parse_field(fields[3], *_FIELD_RANGES[3])
    raw_weekdays, weekday_restricted = _parse_field(fields[4], *_FIELD_RANGES[4])
    # cron 的 7 与 0 同为周日：归一化到 0，避免同一槽位被判两次
    weekdays = frozenset(0 if value == 7 else value for value in raw_weekdays)
    return CronExpression(
        expression=expression,
        minutes=minutes,
        hours=hours,
        days=days,
        months=months,
        weekdays=weekdays,
        day_restricted=day_restricted,
        weekday_restricted=weekday_restricted,
    )


def resolve_timezone(name: str) -> ZoneInfo:
    """解析时区名（IANA），非法即拒绝（FR-40：时区是调度语义的一部分）。"""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleValidationError(f"时区非法：{name!r}（需 IANA 名称，如 Asia/Shanghai）") from exc


def _matches_day(expr: CronExpression, day: datetime) -> bool:
    """日 / 星期匹配（两者都受限时按 cron 标准取 OR）。"""
    if day.month not in expr.months:
        return False
    cron_weekday = (day.weekday() + 1) % 7  # cron: 0=周日；Python: 0=周一
    dom_ok = day.day in expr.days
    dow_ok = cron_weekday in expr.weekdays
    if expr.day_restricted and expr.weekday_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def _times_of_day(expr: CronExpression, day: datetime, tz: ZoneInfo) -> list[datetime]:
    """某天内的全部触发时刻（升序）。"""
    return [
        datetime.combine(day.date(), time(hour, minute), tzinfo=tz)
        for hour in sorted(expr.hours)
        for minute in sorted(expr.minutes)
    ]


def next_fire_time(expression: str, timezone: str, after: datetime) -> datetime:
    """严格晚于 `after` 的下一个触发时刻（FR-40）。

    Raises:
        ScheduleValidationError: 一年内无匹配时刻（如 2 月 30 日）。
    """
    expr = parse_cron(expression)
    tz = resolve_timezone(timezone)
    moment = after.astimezone(tz)
    day = moment
    for _ in range(MAX_SEARCH_DAYS):
        if _matches_day(expr, day):
            for candidate in _times_of_day(expr, day, tz):
                if candidate > moment:
                    return candidate
        day += timedelta(days=1)
    raise ScheduleValidationError(
        f"cron 表达式 {expression!r} 在 {MAX_SEARCH_DAYS} 天内无触发时刻（不可达表达式）"
    )


def previous_fire_time(expression: str, timezone: str, before: datetime) -> datetime:
    """严格早于 `before` 的最近一个触发时刻（到期扫描用；无需遍历全部历史槽位）。

    Raises:
        ScheduleValidationError: 一年内无匹配时刻。
    """
    expr = parse_cron(expression)
    tz = resolve_timezone(timezone)
    moment = before.astimezone(tz)
    day = moment
    for _ in range(MAX_SEARCH_DAYS):
        if _matches_day(expr, day):
            for candidate in reversed(_times_of_day(expr, day, tz)):
                if candidate < moment:
                    return candidate
        day -= timedelta(days=1)
    raise ScheduleValidationError(
        f"cron 表达式 {expression!r} 在 {MAX_SEARCH_DAYS} 天内无触发时刻（不可达表达式）"
    )


def validate_schedule_input(payload: ScheduleInput) -> None:
    """调度参数校验（FR-40/41：非法参数拒绝落库，不静默降级）。

    Raises:
        ScheduleValidationError: cron / 时区 / 模式 / misfire / 水印契约非法。
    """
    parse_cron(payload.cron)
    resolve_timezone(payload.timezone)
    if payload.mode not in ALLOWED_MODES:
        raise ScheduleValidationError(
            f"同步模式非法：{payload.mode!r}，可选 {sorted(ALLOWED_MODES)}"
        )
    if payload.misfire_policy not in ALLOWED_MISFIRE_POLICIES:
        raise ScheduleValidationError(
            f"misfire 策略非法：{payload.misfire_policy!r}，可选 {sorted(ALLOWED_MISFIRE_POLICIES)}"
        )
    if payload.misfire_grace_seconds <= 0:
        raise ScheduleValidationError("misfire 宽限必须为正秒数（FR-40）")
    if payload.mode == MODE_WATERMARK_INCREMENTAL:
        if not payload.watermark_column:
            raise ScheduleValidationError("WATERMARK_INCREMENTAL 必须声明 watermark_column（FR-41）")
        if payload.watermark_lag_seconds is None or payload.watermark_lag_seconds < 0:
            raise ScheduleValidationError(
                "WATERMARK_INCREMENTAL 必须声明非负 watermark_lag_seconds（FR-41）"
            )


async def upsert_schedule(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    payload: ScheduleInput,
) -> PipelineSchedule:
    """创建 / 更新管线调度策略（项目内一管线一策略，SPEC 5.2 `pipeline_id` UK）。

    Raises:
        ScheduleValidationError: 参数非法。
        ScheduleStateConflict: 管线不存在或跨项目。
    """
    validate_schedule_input(payload)
    pipeline = await session.get(Pipeline, pipeline_id)
    if pipeline is None or pipeline.project_id != project_id:
        raise ScheduleStateConflict("管线不存在或不属于当前项目（FR-66 隔离）")
    schedule = (
        (
            await session.execute(
                select(PipelineSchedule).where(PipelineSchedule.pipeline_id == pipeline_id)
            )
        )
        .scalars()
        .first()
    )
    if schedule is None:
        schedule = PipelineSchedule(pipeline_id=pipeline_id, cron=payload.cron, timezone=payload.timezone, mode=payload.mode, misfire_policy=payload.misfire_policy)
        session.add(schedule)
    schedule.cron = payload.cron
    schedule.timezone = payload.timezone
    schedule.mode = payload.mode
    schedule.misfire_policy = payload.misfire_policy
    schedule.misfire_grace_seconds = payload.misfire_grace_seconds
    schedule.watermark_column = payload.watermark_column
    schedule.watermark_initial = payload.watermark_initial
    schedule.watermark_lag_seconds = payload.watermark_lag_seconds
    await session.flush()
    return schedule


async def load_schedule(
    session: AsyncSession, *, project_id: uuid.UUID, pipeline_id: uuid.UUID
) -> PipelineSchedule:
    """按管线取调度策略（不存在即 404，不泄露跨项目存在性）。

    Raises:
        ScheduleNotFound: 调度策略不存在或管线跨项目。
    """
    pipeline = await session.get(Pipeline, pipeline_id)
    if pipeline is None or pipeline.project_id != project_id:
        raise ScheduleNotFound("调度策略不存在")
    schedule = (
        (
            await session.execute(
                select(PipelineSchedule).where(PipelineSchedule.pipeline_id == pipeline_id)
            )
        )
        .scalars()
        .first()
    )
    if schedule is None:
        raise ScheduleNotFound("调度策略不存在")
    return schedule


async def list_schedules(
    session: AsyncSession, *, project_id: uuid.UUID
) -> list[PipelineSchedule]:
    """列出项目内全部调度策略（按管线名排序，便于运行中心展示）。"""
    result = await session.execute(
        select(PipelineSchedule)
        .join(Pipeline, PipelineSchedule.pipeline_id == Pipeline.id)
        .where(Pipeline.project_id == project_id)
        .order_by(Pipeline.name)
    )
    return list(result.scalars().all())


async def delete_schedule(
    session: AsyncSession, *, project_id: uuid.UUID, pipeline_id: uuid.UUID
) -> None:
    """删除调度策略（幂等：不存在即 404）。

    Raises:
        ScheduleNotFound: 调度策略不存在。
    """
    schedule = await load_schedule(session, project_id=project_id, pipeline_id=pipeline_id)
    await session.delete(schedule)
    await session.flush()


async def due_slot(
    schedule: PipelineSchedule, *, now: datetime
) -> tuple[datetime, MisfireDecision]:
    """判定策略在 `now` 之前的最近到期槽位与 misfire 裁决（FR-40）。

    锚点取 `created_at`：策略创建之前的槽位不属于本次调度的事实范围。
    """
    anchor = schedule.created_at or now
    anchor = anchor if anchor.tzinfo else anchor.replace(tzinfo=UTC)
    try:
        slot = previous_fire_time(schedule.cron, schedule.timezone, now)
    except ScheduleValidationError:
        slot = None
    if slot is None or slot <= anchor:
        raise ScheduleNotFound("当前无已到期槽位")
    decision = decide_misfire(
        policy=schedule.misfire_policy,
        scheduled_for=slot,
        now=now,
        grace_seconds=schedule.misfire_grace_seconds,
    )
    return slot, decision


async def _latest_frozen_version(
    session: AsyncSession, pipeline_id: uuid.UUID
) -> PipelineVersion | None:
    """取管线最新已冻结制品的版本（调度触发只能针对可执行版本，FR-31）。"""
    result = await session.execute(
        select(PipelineVersion)
        .where(
            PipelineVersion.pipeline_id == pipeline_id,
            PipelineVersion.artifact_sha256.is_not(None),
        )
        .order_by(PipelineVersion.version_number.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _inherit_execution_facts(
    session: AsyncSession, version_id: uuid.UUID
) -> tuple[str, str, str] | None:
    """继承历史准备单的环境 / 工具事实（调度不新造环境与工具版本）。

    调度策略不存 environment / tool_name / tool_version（SPEC 5.2 无此列），
    故复用该版本最近一次准备单登记的事实——保证调度触发的指纹与手工执行同源。
    """
    result = await session.execute(
        select(ExecutionPreparation)
        .where(ExecutionPreparation.pipeline_version_id == version_id)
        .order_by(ExecutionPreparation.created_at.desc())
        .limit(1)
    )
    preparation = result.scalars().first()
    if preparation is None:
        return None
    return (
        preparation.environment or "",
        preparation.tool_name or "",
        preparation.tool_version or "",
    )


async def _system_requester(session: AsyncSession, project_id: uuid.UUID) -> uuid.UUID | None:
    """调度触发的请求人：项目最早的管理员（系统代发起，登记可追溯的治理事实）。"""
    result = await session.execute(
        select(ProjectMembership)
        .where(ProjectMembership.project_id == project_id, ProjectMembership.role == "ADMIN")
        .order_by(ProjectMembership.created_at)
        .limit(1)
    )
    membership = result.scalars().first()
    return membership.user_id if membership else None


def build_watermark_window(
    schedule: PipelineSchedule, *, now: datetime, previous: str | None = None
) -> WatermarkWindow | None:
    """构造本次增量窗口（仅 WATERMARK_INCREMENTAL 模式；FR-41）。"""
    if schedule.mode != MODE_WATERMARK_INCREMENTAL or not schedule.watermark_column:
        return None
    return resolve_window(
        column=schedule.watermark_column,
        initial=schedule.watermark_initial,
        lag_seconds=schedule.watermark_lag_seconds or 0,
        previous=previous,
        now=now,
    )


def record_schedule_context(
    preparation: ExecutionPreparation,
    *,
    schedule: PipelineSchedule,
    scheduled_for: datetime,
    window: WatermarkWindow | None,
) -> None:
    """把调度事实（槽位 / 触发方式 / 水印窗口）追加进准备单的影响报告。

    落点选择：`impact_report` 是准备单既有的治理事实 JSON 列（S6 起用于登记
    `requested_input`），**不新增列、不改指纹口径**（`input_fingerprint` 不含本字段），
    也不进入任何执行响应正文（AC-02）。
    """
    report = dict(preparation.impact_report or {})
    requested = dict(report.get("requested_input") or {})
    requested["schedule_context"] = {
        "schedule_id": str(schedule.id),
        "pipeline_id": str(schedule.pipeline_id),
        "scheduled_for": scheduled_for.isoformat(),
        "trigger": "SCHEDULED",
        "misfire_policy": schedule.misfire_policy,
        "watermark_window": window.as_payload() if window is not None else None,
    }
    report["requested_input"] = requested
    preparation.impact_report = report


async def scan_due_schedules(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    prepare_service: PrepareService | None = None,
) -> ScheduleScanSummary:
    """扫描一遍调度策略：到期槽位 → misfire 裁决 → 创建受管准备单（FR-40/41）。

    Args:
        session: Worker / 请求级会话（调用方负责提交）。
        settings: 应用配置；缺省全局配置。
        now: 当前时刻（测试可注入）。
        prepare_service: Harness 准备单服务；缺省取应用生命周期装配的实例。

    Returns:
        ScheduleScanSummary: 本遍评估 / 触发 / 跳过 / 推迟计数与原因摘要
        （不含凭据、HOCON 正文，AC-02）。
    """
    effective = settings or default_settings
    moment = now or datetime.now(UTC)
    summary = ScheduleScanSummary()
    schedules = (await session.execute(select(PipelineSchedule))).scalars().all()
    for schedule in schedules:
        summary.evaluated += 1
        try:
            slot, decision = await due_slot(schedule, now=moment)
        except ScheduleNotFound:
            continue
        if not decision.fire:
            summary.skipped += 1
            summary.details.append(f"schedule {schedule.id}: {decision.reason}")
            continue
        version = await _latest_frozen_version(session, schedule.pipeline_id)
        if version is None:
            summary.deferred += 1
            summary.details.append(f"schedule {schedule.id}: 管线无可执行（已冻结）版本")
            continue
        facts = await _inherit_execution_facts(session, version.id)
        if facts is None:
            summary.deferred += 1
            summary.details.append(
                f"schedule {schedule.id}: 该版本无历史准备单，无法继承环境 / 工具事实"
            )
            continue
        pipeline = await session.get(Pipeline, schedule.pipeline_id)
        if pipeline is None:
            continue
        requester = await _system_requester(session, pipeline.project_id)
        if requester is None:
            summary.deferred += 1
            summary.details.append(f"schedule {schedule.id}: 项目无管理员，无法登记请求人")
            continue
        environment, tool_name, tool_version = facts
        window = build_watermark_window(schedule, now=moment)
        preparation, _decision = await _create_preparation(
            session,
            prepare_service=prepare_service,
            effective=effective,
            project_id=pipeline.project_id,
            version_id=version.id,
            requester=requester,
            schedule=schedule,
            slot=slot,
            environment=environment,
            tool_name=tool_name,
            tool_version=tool_version,
            window=window,
        )
        if preparation is None:
            summary.deferred += 1
            summary.details.append(f"schedule {schedule.id}: 准备单创建被拒（PDP DENY）")
            continue
        summary.fired += 1
        summary.details.append(
            f"schedule {schedule.id}: 槽位 {slot.isoformat()} → 准备单 {preparation.id}"
        )
    return summary


async def _create_preparation(
    session: AsyncSession,
    *,
    prepare_service: PrepareService | None,
    effective: Settings,
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    requester: uuid.UUID,
    schedule: PipelineSchedule,
    slot: datetime,
    environment: str,
    tool_name: str,
    tool_version: str,
    window: WatermarkWindow | None,
) -> tuple[ExecutionPreparation | None, object | None]:
    """创建调度触发的准备单（幂等键绑定槽位；PDP DENY 返回 None 而非抛异常）。

    幂等键 `sched-<schedule_id>-<槽位时刻>`：同一槽位重复扫描只会得到同一张准备单，
    既实现 FIRE_ONCE 的「只补一次」，也让 SKIP 无需额外状态列（禁止事项 7）。
    """
    from etl_agent.harness.errors import PolicyDeny

    service = prepare_service or _default_prepare_service(effective)
    payload = PrepareInput(
        pipeline_version_id=version_id,
        idempotency_key=f"sched-{schedule.id}-{slot.isoformat()}",
        environment=environment,
        tool_name=tool_name,
        tool_version=tool_version,
        actor_project_id=project_id,
    )
    try:
        preparation, decision = await service.create_preparation(
            session, project_id=project_id, payload=payload, requested_by=requester
        )
    except PolicyDeny:
        # PDP DENY 无放行通道（FR-29）：调度不绕过策略，记录后等下一个槽位
        return None, None
    record_schedule_context(
        preparation, schedule=schedule, scheduled_for=slot, window=window
    )
    await session.flush()
    return preparation, decision


def _default_prepare_service(effective: Settings) -> PrepareService:
    """按配置装配准备单服务（Worker 任务无请求上下文，故在此按需构建）。

    策略包必须验签通过——不可信的 PDP 不放行任何调度触发（FR-30）。
    """
    from etl_agent.harness.pdp.engine import PdpEngine
    from etl_agent.harness.policy.package import load_package

    package = load_package(
        effective.policy_package_path,
        effective.policy_package_signature_path,
        effective.harness_public_key_list,
    )
    return PrepareService(
        PdpEngine(package), requested_ttl_seconds=effective.harness_preparation_ttl_seconds
    )


__all__ = [
    "ALLOWED_MODES",
    "DEFAULT_TIMEZONE",
    "MODE_BATCH",
    "MODE_WATERMARK_INCREMENTAL",
    "CronExpression",
    "ScheduleInput",
    "ScheduleScanSummary",
    "build_watermark_window",
    "delete_schedule",
    "due_slot",
    "list_schedules",
    "load_schedule",
    "next_fire_time",
    "parse_cron",
    "previous_fire_time",
    "record_schedule_context",
    "resolve_timezone",
    "scan_due_schedules",
    "upsert_schedule",
    "validate_schedule_input",
]
