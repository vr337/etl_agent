"""M-L 根因诊断 — 诊断域错误体系（复用 SPEC 6.4 既有码，不新增）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-L
覆盖 FR：FR-47（可解释根因诊断）
对应 AC：AC-08（诊断不可用时显式失败，不返回空诊断）

口径：S8 提示词 §8.14 列出了 `BIZ_422_DIAGNOSIS_UNAVAILABLE`——它描述的是
「诊断服务不可用（拿不到引擎错误事实，无法给出结论）」这一**运行期依赖缺失**，
而非业务状态冲突。本模块按提示词保留该码，并明确其语义边界：

- 运行**没有失败**时**不是**「诊断不可用」，而是「无需诊断」（返回 None，不报错）；
- 有失败事实但诊断链路不可用（如引擎侧摘要缺失且无法分类）才返回本码。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class DiagnosisUnavailable(BizError):
    """诊断不可用：缺少可判定的失败事实，无法给出可解释结论。"""

    code = "BIZ_422_DIAGNOSIS_UNAVAILABLE"
    http_status = 422


__all__ = ["DiagnosisUnavailable"]
