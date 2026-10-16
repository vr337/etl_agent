"""M-K 执行与数据面 — 影子表预复制 + 错误表（Doris DDL，S7 只创建不 Swap）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 6.1 章 API-14 / FR-43 / FR-44
覆盖 FR：FR-43（影子表与原子 Swap 的影子表预复制半边）、FR-44（错误分流错误表）
对应 AC：AC-06（有效数据落入影子表、错误数据分流写入错误表）、AC-07 前置（原子 Swap 在 S8）

S7 边界（提示词 §8.6 / 禁止事项 16）：**只做影子表 / 错误表创建 + 写入目标改写**，
不执行 `REPLACE TABLE ... PROPERTIES('swap'='true')`——原子发布属 S8（质量报告
PASSED 之后由监督链路触发）。

T-SHADOW-01：新增**错误表**通道（`<target>_error`），承载未命中质量规则的行
（原始值 + 违规原因，PRD 10.5 #4「错误表例外条款」）；两张表都在执行通道内**幂等创建**。

执行通道抽象：`ShadowTableExecutor` 协议 + `LoggingShadowTableExecutor`
（无 Doris 的开发/测试环境：只记录 DDL，不连目标端）+ `DorisShadowTableExecutor`
（经 Doris FE MySQL 协议执行 DDL）。目标端 Doris 是否可用由部署决定，
控制面不猜测——不可用时执行失败可观测（BIZ_503 家族由调用方映射）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# 影子表后缀（FR-43：与目标表同 Schema，命名可追溯）
SHADOW_SUFFIX = "_shadow"

# 错误表后缀（T-SHADOW-01 / FR-44：未命中质量规则的行分流写入错误表）
ERROR_SUFFIX = "_error"


def shadow_table_name(target_table: str) -> str:
    """影子表名：`<target>_shadow`（同 Schema 强校验由 S8 Swap 承担）。"""
    return f"{target_table}{SHADOW_SUFFIX}"


def error_table_name(target_table: str) -> str:
    """错误表名：`<target>_error`（与质量契约 error_sink 同源命名，T-SHADOW-02 统一）。"""
    return f"{target_table}{ERROR_SUFFIX}"


def create_shadow_table_sql(target_table: str) -> str:
    """影子表预复制 DDL（Doris `CREATE LIKE`，**幂等**）。

    `CREATE TABLE IF NOT EXISTS <target>_shadow LIKE <target>` 复制结构与分区属性，
    但不复制数据——有效数据由 SeaTunnel 作业写入（质量契约 valid_sink 指向影子表）。

    `IF NOT EXISTS` 是必需的（T-SHADOW-01 run21 教训）：影子表可能已被 e2e init SQL
    预建（`doris-init/01-init.sql`），而每次运行都会走本通道；缺了它 Doris 直接报
    `Table 'order_wide_shadow' already exists` 并把作业拦在提交前。
    """
    return f"CREATE TABLE IF NOT EXISTS {shadow_table_name(target_table)} LIKE {target_table};"


def create_error_table_sql(target_table: str, columns: list[dict[str, Any]]) -> str:
    """错误表 DDL（T-SHADOW-01，幂等）。

    **为何运行期建表而非 e2e init SQL**：init SQL 属演示 seed（改动红线），且错误表是
    **执行期产物**（目标表随运行而定），与影子表同属 M-K 数据面，应在执行通道内幂等创建。

    **为何不能用 `LIKE <目标表>`**：目标表的业务列带 NOT NULL（如 `amount NOT NULL`），
    而脏行恰恰是 `amount IS NULL`——照抄结构会把脏行**再拒一次**，错误表就永远收不到数据。
    故业务列一律显式声明为 **NULL**，再追加两列违规追踪列（GATE-006 traceability 要求：
    来源行标识 + 错误码 + 违规原因；前两者由业务列承载，此处补后两者）。

    列清单取自 ETL 计划的 `sink.target_schema.columns`（生成侧已声明，无需查库）。
    """
    business = ",\n  ".join(
        f"`{column.get('name')}` {column.get('type')} NULL" for column in columns
    )
    return (
        f"CREATE TABLE IF NOT EXISTS `{error_table_name(target_table)}` (\n"
        f"  {business},\n"
        "  `error_code` VARCHAR(64) NOT NULL COMMENT '质量规则错误码（rules[].code）',\n"
        "  `error_reason` VARCHAR(255) NOT NULL COMMENT '违规原因'\n"
        ")\n"
        "ENGINE = OLAP\n"
        f"DUPLICATE KEY (`{columns[0].get('name') if columns else 'id'}`)\n"
        f"DISTRIBUTED BY HASH (`{columns[0].get('name') if columns else 'id'}`) BUCKETS 2\n"
        'PROPERTIES ("replication_num" = "1");'
    )


class ShadowTableExecutor(ABC):
    """数据面执行通道（影子表 / 错误表的创建与清理）。"""

    @abstractmethod
    async def ensure_shadow_table(self, target_table: str) -> str:
        """确保影子表存在并返回其名称（已存在则幂等跳过）。"""

    @abstractmethod
    async def ensure_error_table(self, target_table: str, columns: list[dict[str, Any]]) -> str:
        """确保错误表存在并返回其名称（T-SHADOW-01：幂等，DDL 见 `create_error_table_sql`）。"""

    @abstractmethod
    async def drop_shadow_table(self, target_table: str) -> None:
        """清理影子表（失败善后：未发布脏数据的回收，S8 回滚链路亦用）。"""


class LoggingShadowTableExecutor(ShadowTableExecutor):
    """开发/测试通道：只记录 DDL 不连目标端（无 Doris 时可观测地推进）。"""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.errors_created: list[str] = []
        self.dropped: list[str] = []

    async def ensure_shadow_table(self, target_table: str) -> str:
        shadow = shadow_table_name(target_table)
        if shadow not in self.created:
            self.created.append(shadow)
        return shadow

    async def ensure_error_table(self, target_table: str, columns: list[dict[str, Any]]) -> str:
        error_table = error_table_name(target_table)
        if error_table not in self.errors_created:
            self.errors_created.append(error_table)
        return error_table

    async def drop_shadow_table(self, target_table: str) -> None:
        self.dropped.append(shadow_table_name(target_table))


class DorisShadowTableExecutor(ShadowTableExecutor):
    """Doris FE MySQL 协议通道（生产目标态；aiomysql 已为 S3 探查依赖）。"""

    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        # 口令只用于建立一次性连接，不落日志（AC-02）
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database

    async def _execute(self, statement: str) -> None:
        import aiomysql

        conn = await aiomysql.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            db=self._database,
        )
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(statement)
        finally:
            conn.close()

    async def ensure_shadow_table(self, target_table: str) -> str:
        await self._execute(create_shadow_table_sql(target_table))
        return shadow_table_name(target_table)

    async def ensure_error_table(self, target_table: str, columns: list[dict[str, Any]]) -> str:
        await self._execute(create_error_table_sql(target_table, columns))
        return error_table_name(target_table)

    async def drop_shadow_table(self, target_table: str) -> None:
        await self._execute(f"DROP TABLE IF EXISTS {shadow_table_name(target_table)};")
