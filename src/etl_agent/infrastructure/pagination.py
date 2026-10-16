"""列表端点共享助手（联调修复新增，fix/S10-e2e-integration）。

背景：S2~S8 各域只实现了「写」端点（POST），而前端控制台的列表展示普遍按 RESTful
惯例假设 `GET /projects/{id}/<resource>` 存在——真实联调时这些请求全部落到 405
Method Not Allowed。本模块收口「分页 + 总数」的通用实现，新增列表端点只写业务
过滤，不重复分页样板。

设计约束（与 `GET /executions` / `GET /preparations` 一致）：
- 列表体返回**裸数组**——与前端既有消费方式对齐（前端 `listXxx` 直接 `return data`）；
- 同过滤条件的**总数**经 `X-Total-Count` 响应头回传，避免改变响应体形状。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


def clamp_limit_offset(limit: int, offset: int, *, maximum: int = 200) -> tuple[int, int]:
    """夹紧分页参数（路由层 Query 已约束，此处兜底防御性调用）。"""
    return max(1, min(limit, maximum)), max(0, offset)


async def paginate(
    session: AsyncSession,
    base: Select[*tuple[Any, ...]],
    *,
    order_by: Sequence[Any],
    limit: int,
    offset: int,
) -> tuple[list[tuple[Any, ...]], int]:
    """按给定 base 查询分页页 + 同条件总数。

    Args:
        session: 请求级数据库会话。
        base: 已含全部 WHERE 条件的 `select(...)`（**不含** order_by / limit / offset）。
        order_by: 排序键序列（调用方保证稳定序：末位须有唯一键兜底，如主键）。
        limit: 单页条数。
        offset: 偏移。

    Returns:
        (rows, total)：rows 为 SQLAlchemy 行元组列表，total 为分页前总数。
    """
    limit, offset = clamp_limit_offset(limit, offset)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await session.execute(base.order_by(*order_by).limit(limit).offset(offset))).all()
    return list(rows), int(total)


async def exists_project(session: AsyncSession, project_id: uuid.UUID) -> bool:
    """项目是否存在（仅用于测试与诊断，不用于鉴权——鉴权由依赖层统一处理）。"""
    from etl_agent.infrastructure.models import Project

    count = (
        await session.execute(
            select(func.count()).select_from(Project).where(Project.id == project_id)
        )
    ).scalar_one()
    return bool(count)
