"""M-G 确定性门禁 — GATE-001 禁明文凭据。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 8 章「安全 · 凭据保护」
覆盖 FR：FR-19（禁明文凭据契约）
对应 AC：AC-02（无敏感明文泄露）

判定：HOCON 中不得出现 `password=xxx` 字面量；plan 中不得出现明文凭据字段。
`${ENV_VAR}` 占位符是**允许**的——这正是 FR-68「图内只传占位符、运行期变量 Worker 侧物化」的实现方式。
失败摘要只回字段名，不回值本身（否则等于把凭据写进门禁摘要，自破 AC-02）。
"""
from __future__ import annotations

import re
from typing import Any

from etl_agent.gate.contracts import SEVERITY_ERROR, Context, Plan, RuleResult

CREDENTIAL_KEY = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)\b"
)
# 赋值语句：key = value / key: value，值可为双引号 / 单引号字符串或裸字面量
ASSIGNMENT = re.compile(
    r"""(?im)(?P<key>[A-Za-z_][A-Za-z0-9_.\-]*)\s*[=:]\s*"""
    r"""(?:"(?P<dq>[^"\n]*)"|'(?P<sq>[^'\n]*)'|(?P<raw>[^\s,}\]]+))"""
)
PLACEHOLDER = re.compile(r"^\$\{[^}]{1,200}\}$")


def _is_placeholder(value: str) -> bool:
    """`${...}` 占位符 / 环境变量引用视为合规（不含秘密本身）。"""
    stripped = value.strip()
    return bool(PLACEHOLDER.match(stripped))


def _assigned_value(match: re.Match[str]) -> str:
    """取赋值右侧的**字面量内容**（去掉包裹引号，便于识别 `${}` 占位符）。"""
    for group in ("dq", "sq", "raw"):
        value = match.group(group)
        if value is not None:
            return str(value)
    return ""


def first_plaintext_credential(hocon: str) -> str | None:
    """返回 HOCON 中首个明文凭据字段名（无则 None）。

    跨模块出口：GATE-001 与 Harness PDP（`HARNESS-P0-PLAINTEXT-CREDENTIAL`）共用同一份
    检测口径——两处各写一份正则迟早会漂移，而这是 AC-02 的判定依据。
    """
    for match in ASSIGNMENT.finditer(hocon):
        key = match.group("key")
        if not CREDENTIAL_KEY.search(key):
            continue
        if _is_placeholder(_assigned_value(match)):
            continue
        return key
    return None


class NoPlaintextCredentials:
    """GATE-001：HOCON 与 plan 均不得携带明文凭据。"""

    rule_id = "GATE-001"
    description = "禁明文凭据"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        leaked_key = first_plaintext_credential(hocon)
        if leaked_key is not None:
            return RuleResult.of(
                self, False, f"HOCON 中出现明文凭据字段：{leaked_key}（值已省略，AC-02）"
            )
        leaked = _first_plaintext_key(plan)
        if leaked is not None:
            return RuleResult.of(self, False, f"EtlPlan 中出现明文凭据字段：{leaked}（值已省略，AC-02）")
        return RuleResult.of(self, True, "未发现明文凭据（凭据一律走 ${} 占位符）")


def _first_plaintext_key(node: Any, path: str = "") -> str | None:
    """递归查找 plan 中携带非占位符值的凭据字段，返回其路径（不含值）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if (
                CREDENTIAL_KEY.search(str(key))
                and isinstance(value, str)
                and value.strip()
                and not _is_placeholder(value)
            ):
                return child
            found = _first_plaintext_key(value, child)
            if found is not None:
                return found
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found = _first_plaintext_key(item, f"{path}[{index}]")
            if found is not None:
                return found
    return None
