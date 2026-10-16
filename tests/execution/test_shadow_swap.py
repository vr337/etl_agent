"""影子表预复制 + 错误表用例（T-13.11 / FR-43 / FR-44）。

覆盖：`CREATE TABLE <target>_shadow LIKE <target>` DDL 正确、幂等创建、
**S7 不做原子 Swap**（禁止事项 16：Swap 属 S8）；
T-SHADOW-01 错误表（`<target>_error`）：命名、幂等、业务列可空 + 两列违规追踪列。
"""

from __future__ import annotations

import ast
import inspect

from etl_agent.execution import shadow as shadow_module
from etl_agent.execution.shadow import (
    ERROR_SUFFIX,
    SHADOW_SUFFIX,
    LoggingShadowTableExecutor,
    create_error_table_sql,
    create_shadow_table_sql,
    error_table_name,
    shadow_table_name,
)


def test_shadow_table_name_suffix() -> None:
    """影子表命名：`<target>_shadow`（与目标表同 Schema，命名可追溯）。"""
    assert SHADOW_SUFFIX == "_shadow"
    assert shadow_table_name("orders_wide") == "orders_wide_shadow"


def test_create_shadow_table_sql_is_create_like_and_idempotent() -> None:
    """预复制 DDL：Doris `CREATE LIKE` + **`IF NOT EXISTS` 幂等**（T-SHADOW-01 run21 教训）。

    影子表可能已被 e2e init SQL 预建（`doris-init/01-init.sql:43-44`），而每次运行
    都会走本通道；缺 `IF NOT EXISTS` 会让 Doris 报 `Table '..._shadow' already exists`
    并把作业拦在**提交前**（run21 实证：引擎从未被调用）。
    """
    sql = create_shadow_table_sql("orders_wide")
    assert sql == "CREATE TABLE IF NOT EXISTS orders_wide_shadow LIKE orders_wide;"
    assert "IF NOT EXISTS" in sql
    assert " LIKE " in sql


def test_data_plane_ddl_is_idempotent_on_both_tables() -> None:
    """幂等是 T-SHADOW-01 的基础：影子表与错误表**两处建表必须同族一致**（都幂等）。

    run21 曾因**只有影子表**漏 `IF NOT EXISTS` 而在提交前失败；错误表 DDL 自始即带。
    两处一起断言，防止以后再只修一处。
    """
    assert "IF NOT EXISTS" in create_shadow_table_sql("orders_wide")
    error_ddl = create_error_table_sql("orders_wide", [{"name": "id", "type": "bigint"}])
    assert "IF NOT EXISTS" in error_ddl


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


def test_error_table_name_suffix() -> None:
    """T-SHADOW-01：错误表命名 `<target>_error`（与质量契约 error_sink 同源）。"""
    assert ERROR_SUFFIX == "_error"
    assert error_table_name("orders_wide") == "orders_wide_error"


def test_create_error_table_sql_is_idempotent_and_nullable() -> None:
    """T-SHADOW-01 错误表 DDL：幂等 + 业务列**全可空** + 两列违规追踪列。

    业务列必须可空——脏行恰恰是 `amount IS NULL`，若照抄目标表的 NOT NULL
    结构，错误表会把脏行**再拒一次**（这正是不能用 `CREATE ... LIKE` 的原因）。
    """
    sql = create_error_table_sql(
        "orders_wide",
        [
            {"name": "order_id", "type": "bigint"},
            {"name": "amount", "type": "decimal(18, 2)"},
        ],
    )
    assert "CREATE TABLE IF NOT EXISTS `orders_wide_error`" in sql
    assert "`order_id` bigint NULL" in sql
    assert "`amount` decimal(18, 2) NULL" in sql
    # 业务列部分不得出现 NOT NULL（只有两列追踪列是 NOT NULL）
    assert "NOT NULL" not in sql.split("`error_code`")[0]
    assert "`error_code` VARCHAR(64) NOT NULL" in sql
    assert "`error_reason` VARCHAR(255) NOT NULL" in sql


async def test_ensure_error_table_idempotent() -> None:
    """重复确保只记一次（幂等建表，避免重复 DDL）。"""
    executor = LoggingShadowTableExecutor()
    columns = [{"name": "order_id", "type": "bigint"}]
    first = await executor.ensure_error_table("orders_wide", columns)
    second = await executor.ensure_error_table("orders_wide", columns)
    assert first == second == "orders_wide_error"
    assert executor.errors_created == ["orders_wide_error"]


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
    # 禁的是**原子发布**形态：`REPLACE TABLE ... PROPERTIES('swap')`。
    # 普通建表属性（`PROPERTIES ("replication_num" = "1")`，单 BE 集群必需，错误表 DDL
    # 亦用）不属发布语义，不在禁令内。
    assert not any("REPLACE TABLE" in text or "'swap'" in text for text in literals)
    # 只创建影子表 / 错误表（DROP 仅用于失败善后，不涉及发布）
    assert any("CREATE TABLE" in text for text in literals)


def test_doris_executor_uses_mysql_protocol() -> None:
    """生产通道走 Doris FE MySQL 协议（aiomysql 已为 S3 探查依赖，不新增驱动）。"""
    executor = shadow_module.DorisShadowTableExecutor(
        host="doris-fe", port=9030, user="etl_writer", password="pw", database="dw"
    )
    assert executor is not None
    assert issubclass(shadow_module.DorisShadowTableExecutor, shadow_module.ShadowTableExecutor)
