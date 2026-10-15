"""M-J 执行 Worker — Worker 侧数据库会话（Celery 任务专用）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 4.3 章进程与入口 / 第 8 章「高可用 · Worker 可靠性」/ ADR-02
覆盖 FR：FR-38（Worker 与 API 同版本部署，共享同一业务库 schema）
对应 AC：AC-06（Worker 事务边界：状态迁移、事件流、质量报告同会话提交）

与请求级会话（`infrastructure.database.get_session`）分离：Worker 任务不在
FastAPI 依赖树内，自行开闭会话；事务提交由服务层显式 `commit()` 控制，
避免「半迁移状态落库、事件流缺失」的不一致窗口。

**每次任务独立建引擎**（不是复用 `infrastructure.database.engine`）：Celery 任务壳用
`asyncio.run()` 每次新建事件循环，而 asyncpg 连接池的底层连接绑定在创建它的循环上——
跨循环复用同一池会拿到「已关闭循环」的连接。每任务 `dispose()` 后连接池随循环一起回收，
代价是每次任务一条 TCP 建连（Beat 2s 节奏下可接受，远小于错连的故障代价）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from etl_agent.infrastructure.database import settings as infra_settings


@asynccontextmanager
async def worker_session() -> AsyncIterator[AsyncSession]:
    """开启一个 Worker 会话（提交 / 回滚由调用方负责；退出即释放引擎）。"""
    engine = create_async_engine(infra_settings.resolved_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


__all__ = ["worker_session"]
