"""SQLAlchemy 声明性基类与通用 mixin（T-02.1）。

约束来源：etl_agent-SPEC_v1.0.3-Baseline 第 5 章 / 9.10 全局约束 / ADR-07：
- 时间戳统一 TIMESTAMP WITH TIME ZONE；
- 约束命名统一（保证 Alembic downgrade 可靠回滚）。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 统一约束命名约定：alembic downgrade 依赖稳定的约束名。
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """全部业务模型（etl_agent 库）的声明性基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """UUID 主键：应用侧生成，插入前即可被引用（无需往返数据库）。"""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    """仅创建时间（追加型/不可变行：事件、快照、报告、制品）。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TimestampMixin(CreatedAtMixin):
    """创建 + 更新时间（可变行）。"""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
