"""M-G 确定性门禁 — GATE-014 逐行 LLM 契约。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 2.2 章第三方依赖（OpenAI 兼容供应商）/ 第 8 章「安全 · 凭据保护」
覆盖 FR：FR-19（逐行 LLM 契约）、FR-24（限速与允许 API 主机清单）
对应 AC：AC-04（不合规候选被拦截）、AC-02（外发主机白名单防止数据外泄）

判定：声明逐行 LLM 转换时，限速不得超过默认上限（10 行/s，FR-24），
且允许的 API 主机必须非空并落在白名单内——白名单外主机等于把行数据外发给未授权方。
"""
from __future__ import annotations

from etl_agent.gate.contracts import SEVERITY_ERROR, Context, Plan, RuleResult

DEFAULT_RATE_LIMIT_PER_SECOND = 10
ALLOWED_LLM_HOSTS = frozenset(
    {
        "api.openai.com",
        "api.deepseek.com",
        "ark.cn-beijing.volces.com",
        "api.moonshot.cn",
        "open.bigmodel.cn",
    }
)


class RowLlmContract:
    """GATE-014：逐行 LLM 限速 + 主机白名单。"""

    rule_id = "GATE-014"
    description = "逐行 LLM 契约"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        row_llm = plan.get("row_llm")
        if row_llm is None:
            return RuleResult.of(self, True, "未启用逐行 LLM 转换")
        if not isinstance(row_llm, dict):
            return RuleResult.of(self, False, "row_llm 必须是对象")
        rate = row_llm.get("rate_limit_per_second", DEFAULT_RATE_LIMIT_PER_SECOND)
        if not isinstance(rate, int) or rate <= 0 or rate > DEFAULT_RATE_LIMIT_PER_SECOND:
            return RuleResult.of(
                self,
                False,
                f"rate_limit_per_second 必须为 1–{DEFAULT_RATE_LIMIT_PER_SECOND} 的整数（FR-24）",
            )
        hosts = row_llm.get("allowed_hosts")
        if not isinstance(hosts, list) or not hosts:
            return RuleResult.of(self, False, "allowed_hosts 必须为非空数组")
        outside = sorted({str(host).lower() for host in hosts} - ALLOWED_LLM_HOSTS)
        if outside:
            return RuleResult.of(self, False, f"allowed_hosts 含白名单外主机：{', '.join(outside)}")
        return RuleResult.of(self, True, f"限速 {rate} 行/s，主机均在白名单内")
