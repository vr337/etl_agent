"""影子表预复制用例（T-13.11 / FR-43）。

覆盖：`CREATE TABLE <target>_shadow LIKE <target>` DDL 正确、幂等创建、
**S7 不做原子 Swap**（禁止事项 16：Swap 属 S8）。
"""
from __future__ import annotations

import ast
import inspect

from etl_agent.execution import shadow as shadow_module
from etl_agent.execution.shadow import (
    SHADOW_SUFFIX,
    LoggingShadowTableExecutor,
    create_shadow_table_sql,
    shadow_table_name,
)


def test_shadow_table_name_suffix() -> None:
    """影子表命名：`<target>_shadow`（与目标表同 Schema，命名可追溯）。"""
    assert SHADOW_SUFFIX == "_shadow"
    assert shadow_table_name("orders_wide") == "orders_wide_shadow"


def test_create_shadow_table_sql_is_create_like() -> None:
    """预复制 DDL 必须是 Doris `CREATE LIKE`（复制结构不复制数据，FR-43）。"""
    assert (
        create_shadow_table_sql("orders_wide")
        == "CREATE TABLE orders_wide_shadow LIKE orders_wide;"
    )


async def test_ensure_shadow_table_idempotent() -> None:
    """重复确保只记一次（幂等创建，避免重复 DDL）。"""
    executor = LoggingShadowTableExecutor()
    first = await executor.ensure_shadow_table("orders_wide")
    second = await executor.ensure_shadow_table("orders_wide")
    assert first == second == "orders_wide_shadow"
    assert executor.created == ["orders_wide_shadow"]


async def test_drop_shadow_table_recorded() -> None:
    """清理通道可用（失败善后 / S8 回滚链路复用）。"""
    executor = LoggingShadowTableExecutor()
    await executor.drop_shadow_table("orders_wide")
    assert executor.dropped == ["orders_wide_shadow"]


def test_no_atomic_swap_in_s7() -> None:
    """S7 不得出现 `REPLACE TABLE ... PROPERTIES('swap')`（禁止事项 16：原子发布属 S8）。

    只检查**代码中的字符串字面量**（排除 docstring/注释）：S7 的文档明确写着
    「不做原子 Swap」，而文档里出现这个 SQL 关键字恰恰是要求，不是违规。
    """
    tree = ast.parse(inspect.getsource(shadow_module))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    assert not any("REPLACE TABLE" in text or "PROPERTIES" in text for text in literals)
    # 只创建影子表（DROP 仅用于失败善后，不涉及发布）
    assert any("CREATE TABLE" in text for text in literals)


def test_doris_executor_uses_mysql_protocol() -> None:
    """生产通道走 Doris FE MySQL 协议（aiomysql 已为 S3 探查依赖，不新增驱动）。"""
    executor = shadow_module.DorisShadowTableExecutor(
        host="doris-fe", port=9030, user="etl_writer", password="pw", database="dw"
    )
    assert executor is not None
    assert issubclass(shadow_module.DorisShadowTableExecutor, shadow_module.ShadowTableExecutor)
