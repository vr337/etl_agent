"""回滚申请 / 审批 / 执行状态机用例（T-16.14，AC-08 核心）。

覆盖：全部状态迁移可达、非法迁移被拒、申请→审批→执行三步、
FAILED 退回可重试、制品状态与申请状态联动、重复申请防冲突。
"""

from __future__ import annotations

from etl_agent.rollback.artifacts import (
    STATUS_AVAILABLE,
    STATUS_ROLLBACK_PENDING,
    STATUS_ROLLED_BACK,
)
from etl_agent.rollback.errors import RollbackStateConflict
from etl_agent.rollback.service import (
    ALL_REQUEST_STATUSES,
    DECISION_APPROVE,
    DECISION_REJECT,
    REQUEST_TRANSITIONS,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_EXECUTED,
    STATUS_EXECUTING,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REJECTED,
    CreateRollbackInput,
    can_transition,
    create_rollback_request,
    decide_rollback,
    is_terminal,
    mark_executed,
    mark_executing,
    mark_failed,
)


def test_all_request_statuses_defined() -> None:
    """7 个申请状态齐备。"""
    assert set(ALL_REQUEST_STATUSES) == {
        "PENDING",
        "APPROVED",
        "REJECTED",
        "EXECUTING",
        "EXECUTED",
        "FAILED",
        "CANCELLED",
    }


def test_state_machine_all_transitions() -> None:
    """迁移白名单逐条可达。"""
    assert can_transition(STATUS_PENDING, STATUS_APPROVED)
    assert can_transition(STATUS_PENDING, STATUS_REJECTED)
    assert can_transition(STATUS_PENDING, STATUS_CANCELLED)
    assert can_transition(STATUS_APPROVED, STATUS_EXECUTING)
    assert can_transition(STATUS_EXECUTING, STATUS_EXECUTED)
    assert can_transition(STATUS_EXECUTING, STATUS_FAILED)
    assert can_transition(STATUS_FAILED, STATUS_EXECUTING)
    # 终态不可再迁移
    for terminal in (STATUS_REJECTED, STATUS_EXECUTED, STATUS_CANCELLED):
        assert is_terminal(terminal) is True
        assert REQUEST_TRANSITIONS[terminal] == frozenset()


async def test_create_decide_execute_flow(db_session, make_run) -> None:
    """AC-08 主线：申请 → 审批 → 执行 → 完成，制品推进到 ROLLED_BACK。"""
    run = await make_run(status="FAILED")
    request = await create_rollback_request(
        db_session,
        payload=CreateRollbackInput(
            run_id=run.id,
            requested_by=run.harness_preparation_id,  # 任意 UUID 主体
            reason="引擎失败需回滚影子表",
            target_table="orders_wide",
            shadow_table="orders_wide_shadow",
        ),
    )
    assert request.status == STATUS_PENDING
    assert request.artifact_status == STATUS_ROLLBACK_PENDING

    await decide_rollback(
        db_session, rollback_id=request.id, decision=DECISION_APPROVE, decided_by=run.id
    )
    assert request.status == STATUS_APPROVED

    await mark_executing(db_session, request.id)
    assert request.status == STATUS_EXECUTING

    await mark_executed(db_session, request.id)
    assert request.status == STATUS_EXECUTED
    assert request.artifact_status == STATUS_ROLLED_BACK


async def test_reject_returns_artifact_to_available(db_session, make_run) -> None:
    """审批 REJECT → 申请终态 REJECTED，制品退回 AVAILABLE（可重新申请）。"""
    run = await make_run(status="FAILED")
    request = await create_rollback_request(
        db_session,
        payload=CreateRollbackInput(
            run_id=run.id, requested_by=run.id, reason="测试", target_table="t"
        ),
    )
    await decide_rollback(
        db_session, rollback_id=request.id, decision=DECISION_REJECT, decided_by=run.id
    )
    assert request.status == STATUS_REJECTED
    assert request.artifact_status == STATUS_AVAILABLE


async def test_mark_failed_returns_artifact_available(db_session, make_run) -> None:
    """执行失败 → FAILED，制品退回 AVAILABLE（保留再回滚能力）。"""
    run = await make_run(status="FAILED")
    request = await create_rollback_request(
        db_session,
        payload=CreateRollbackInput(
            run_id=run.id, requested_by=run.id, reason="测试", target_table="orders_wide"
        ),
    )
    await decide_rollback(
        db_session, rollback_id=request.id, decision=DECISION_APPROVE, decided_by=run.id
    )
    await mark_executing(db_session, request.id)
    await mark_failed(db_session, request.id, reason="doris 不可达")
    assert request.status == STATUS_FAILED
    assert request.artifact_status == STATUS_AVAILABLE


async def test_illegal_transition_rejected(db_session, make_run) -> None:
    """未审批直接执行 → 状态冲突。"""
    run = await make_run(status="FAILED")
    request = await create_rollback_request(
        db_session,
        payload=CreateRollbackInput(
            run_id=run.id, requested_by=run.id, reason="测试", target_table="orders_wide"
        ),
    )
    try:
        await mark_executing(db_session, request.id)
        raise AssertionError("未审批即执行，应被拒绝")
    except RollbackStateConflict:
        pass


async def test_duplicate_pending_rejected(db_session, make_run) -> None:
    """同一运行已有进行中的回滚申请 → 409（防重复申请）。"""
    run = await make_run(status="FAILED")
    payload = CreateRollbackInput(
        run_id=run.id, requested_by=run.id, reason="首次", target_table="orders_wide"
    )
    await create_rollback_request(db_session, payload=payload)
    try:
        await create_rollback_request(db_session, payload=payload)
        raise AssertionError("重复申请应被拒绝")
    except RollbackStateConflict:
        pass


async def test_invalid_manifest_marks_invalid_metadata(db_session, make_run) -> None:
    """制品清单缺必填字段 → INVALID_METADATA。"""
    run = await make_run(status="FAILED")
    request = await create_rollback_request(
        db_session,
        payload=CreateRollbackInput(run_id=run.id, requested_by=run.id, reason="测试"),
        manifest={"entries": []},  # 缺 run_id / target_table / shadow_table
    )
    assert request.artifact_status == "INVALID_METADATA"
