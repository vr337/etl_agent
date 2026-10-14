"""M-F 生成引擎 — 生成域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 6.1 章 API-05/06
覆盖 FR：FR-16（运行不存在 / 状态机不允许）、FR-17（非中断态收到回答）
对应 AC：AC-03（中断与恢复的错误分支）

本文件三个错误码为 S5 新增（SPEC 6.4 未覆盖生成域运行态），已生成 AMEND-004 草稿待人类审核。
跨项目一律按「视同不存在」返回 404，不泄露资源存在性（延续 S3/S4 语义）。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class AgentRunNotFound(BizError):
    """生成运行不存在（跨项目视同不存在，不泄露存在性）。"""

    code = "BIZ_404_AGENT_RUN_NOT_FOUND"
    http_status = 404


class AgentRunStateConflict(BizError):
    """运行状态机不允许当前操作（如已 FAILED 的运行再次澄清）。"""

    code = "BIZ_409_AGENT_RUN_STATE_CONFLICT"
    http_status = 409


class InterruptNotPending(BizError):
    """非中断态收到澄清回答（FR-17：只有 WAITING_INPUT 才可恢复）。"""

    code = "BIZ_422_INTERRUPT_NOT_PENDING"
    http_status = 422
