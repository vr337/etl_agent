"""M-B 项目与工作区治理 — API 请求 / 响应模型。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-01 / 第 3.2 章 M-B
覆盖 FR：FR-01/02/03
对应 AC：AC-01（职责分离）、AC-12（多租户隔离）
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BootstrapRequest(BaseModel):
    """工作区初始化请求体（API-01）。"""

    project_name: str = Field(min_length=1, max_length=255)
    admin_subject: str = Field(min_length=1, max_length=255)


class BootstrapResponse(BaseModel):
    """工作区初始化响应（API-01 出参）。"""

    project_id: uuid.UUID
    admin_user_id: uuid.UUID


class MemberAddRequest(BaseModel):
    """成员添加请求（subject 即身份声明，用户行按需投影创建）。"""

    subject: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=32)


class MemberRoleUpdateRequest(BaseModel):
    """成员角色变更请求（4 类项目成员角色）。"""

    role: str = Field(min_length=1, max_length=32)


class MemberResponse(BaseModel):
    """成员视图。"""

    user_id: uuid.UUID
    subject: str
    display_name: str
    role: str


class RoleGrantRequest(BaseModel):
    """Harness 职责授予请求（5 类职责，granted_by 由服务端从 Actor 注入）。"""

    subject: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=32)


class RoleGrantResponse(BaseModel):
    """Harness 职责授予视图（含授予人留痕，FR-03）。

    granted_by 允许为 None：S1 模型按系统期授予场景保留可空（models.py 不修改）。
    """

    id: uuid.UUID
    user_id: uuid.UUID
    subject: str
    role: str
    granted_by: uuid.UUID | None
    created_at: datetime
