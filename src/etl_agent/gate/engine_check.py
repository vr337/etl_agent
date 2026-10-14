"""M-G 确定性门禁 — 引擎检查抽象与本地 stub（真实 SeaTunnel 检查留 S7）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 7.1 章主流程时序图 / 第 8 章「性能 · 非 LLM 决策路径」
覆盖 FR：FR-20（Worker 侧物化运行期变量后执行 SeaTunnel `--check`，只回传脱敏摘要）
对应 AC：AC-04（门禁 + 有限修复）

S5 范围（提示词 11.6 / 禁止事项 21）：**只提供接口 + 本地 stub**——SeaTunnel 容器 S7 才起，
故 S5 的 stub 只做「HOCON 可解析」级别的基础结构检查，不启动任何引擎进程；
真实 `--check` 在 S7 由 Worker 侧物化变量后执行。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class EngineCheckResult:
    """引擎检查结果（`summary` 为脱敏摘要，不含凭据与 HOCON 正文）。"""

    ok: bool
    checker: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checker": self.checker, "summary": self.summary}


@runtime_checkable
class EngineChecker(Protocol):
    """引擎检查契约（S5 提示词 11.6）。"""

    async def check(self, hocon: str) -> EngineCheckResult:
        """对 HOCON 做引擎侧校验。"""
        ...


class LocalStubEngineChecker:
    """本地 stub：只做基础结构检查（括号配平 / 引号闭合 / 非空）。

    不做语义校验——语义由 14 项静态契约承担；真实 SeaTunnel `--check` 留 S7。
    """

    name = "local-stub"

    async def check(self, hocon: str) -> EngineCheckResult:
        """返回固定的结构检查结论（同一输入恒返回同一结果）。"""
        if not hocon or not hocon.strip():
            return EngineCheckResult(ok=False, checker=self.name, summary="HOCON 为空")
        problem = _first_structural_problem(hocon)
        if problem is not None:
            return EngineCheckResult(ok=False, checker=self.name, summary=problem)
        return EngineCheckResult(
            ok=True, checker=self.name, summary="结构检查通过（真实 SeaTunnel --check 留 S7）"
        )


def _first_structural_problem(hocon: str) -> str | None:
    """括号 / 引号配平检查：返回首个问题的脱敏描述（不返回 HOCON 片段）。"""
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in hocon:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                return f"存在多余的闭合符号（第 {hocon.index(char) + 1} 字符处附近）"
            opened = stack.pop()
            if (opened, char) not in {("{", "}"), ("[", "]")}:
                return "括号类型不匹配"
    if in_string:
        return "字符串引号未闭合"
    if stack:
        return "存在未闭合的块"
    return None
