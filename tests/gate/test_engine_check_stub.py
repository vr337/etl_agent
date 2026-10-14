"""引擎检查 stub 测试（T-10；S5 提示词第十二节 test_engine_check_stub）。

覆盖：本地 stub 对同一输入返回固定结果；结构缺陷（括号未闭合）判失败；
真实 SeaTunnel `--check` 留 S7（S5 不启动引擎进程）。
"""
from __future__ import annotations

from etl_agent.gate.engine_check import EngineCheckResult, LocalStubEngineChecker
from tests.gate.test_rules import COMPLIANT_HOCON


async def test_engine_check_stub() -> None:
    """合规 HOCON → 固定 ok 结果；同输入恒返回同一结论。"""
    checker = LocalStubEngineChecker()
    first = await checker.check(COMPLIANT_HOCON)
    second = await checker.check(COMPLIANT_HOCON)
    assert first == second
    assert isinstance(first, EngineCheckResult)
    assert first.ok
    assert first.checker == "local-stub"
    assert "S7" in first.summary  # 明确标注真实检查留 S7


async def test_engine_check_stub_rejects_broken_structure() -> None:
    """结构缺陷（块未闭合 / 引号未闭合 / 空 HOCON）→ 判失败，摘要不含正文。"""
    checker = LocalStubEngineChecker()
    broken_block = await checker.check('sink {\n  Jdbc {\n    url = "x"\n')
    assert not broken_block.ok
    assert "未闭合" in broken_block.summary

    broken_quote = await checker.check('sink { password = "unterminated }')
    assert not broken_quote.ok

    empty = await checker.check("   ")
    assert not empty.ok
    assert "空" in empty.summary
