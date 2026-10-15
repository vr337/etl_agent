"""M-M 评测与安全进化 — Benchmark 域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（分层仿真评测与版本化 Benchmark）
对应 AC：AC-09（六项门禁）、AC-14（L0）、AC-15（L1）、AC-16（L2）

口径（S9 提示词 §8.10）：新增 3 个 Benchmark 域码：

| 场景 | 码 | 来源 |
|------|----|------|
| Benchmark 运行不存在 / suite 版本不存在 | `BIZ_404_BENCHMARK_RUN_NOT_FOUND` | S9 新增（→ AMEND-008） |
| 运行状态机不允许当前操作（终态重跑等） | `BIZ_409_BENCHMARK_STATE_CONFLICT` | S9 新增（→ AMEND-008） |
| 六项门禁未全部满足 | `BIZ_422_BENCHMARK_GATE_FAILED` | S9 新增（→ AMEND-008） |

复用：`AUTH_403_ROLE_REQUIRED`（仅 SECURITY_APPROVER）、`SYS_503_DEPENDENCY_UNAVAILABLE`（引擎 / Doris 不可达）。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class BenchmarkRunNotFound(BizError):
    """Benchmark 运行或套件版本不存在（不泄露存在性）。"""

    code = "BIZ_404_BENCHMARK_RUN_NOT_FOUND"
    http_status = 404


class BenchmarkStateConflict(BizError):
    """Benchmark 状态机不允许当前操作。"""

    code = "BIZ_409_BENCHMARK_STATE_CONFLICT"
    http_status = 409


class BenchmarkGateFailed(BizError):
    """六项门禁未全部满足（附脱敏的指标实际值，不含制品正文）。"""

    code = "BIZ_422_BENCHMARK_GATE_FAILED"
    http_status = 422


__all__ = ["BenchmarkGateFailed", "BenchmarkRunNotFound", "BenchmarkStateConflict"]
