"""M-K 执行与数据面 — 影子表预复制（Doris `CREATE LIKE`，S7 只创建不 Swap）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 6.1 章 API-14 / FR-43
覆盖 FR：FR-43（影子表与原子 Swap 的影子表预复制半边）
对应 AC：AC-06（有效数据落入影子表）、AC-07 前置（原子 Swap 在 S8）

S7 边界（提示词 §8.6 / 禁止事项 16）：**只做影子表创建 + 写入目标改写**，
不执行 `REPLACE TABLE ... PROPERTIES('swap'='true')`——原子发布属 S8（质量报告
PASSED 之后由监督链路触发）。

执行通道抽象：`ShadowTableExecutor` 协议 + `LoggingShadowTableExecutor`
（无 Doris 的开发/测试环境：只记录 DDL，不连目标端）+ `DorisShadowTableExecutor`
（经 Doris FE MySQL 协议执行 DDL）。目标端 Doris 是否可用由部署决定，
控制面不猜测——不可用时执行失败可观测（BIZ_503 家族由调用方映射）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

# 影子表后缀（FR-43：与目标表同 Schema，命名可追溯）
SHADOW_SUFFIX = "_shadow"


def shadow_table_name(target_table: str) -> str:
    """影子表名：`<target>_shadow`（同 Schema 强校验由 S8 Swap 承担）。"""
    return f"{target_table}{SHADOW_SUFFIX}"


def create_shadow_table_sql(target_table: str) -> str:
    """影子表预复制 DDL（Doris `CREATE LIKE`）。

    `CREATE TABLE <target>_shadow LIKE <target>` 复制结构与分区属性，
    但不复制数据——有效数据由 SeaTunnel 作业写入（质量契约 valid_sink 指向影子表）。
    """
    return f"CREATE TABLE {shadow_table_name(target_table)} LIKE {target_table};"


class ShadowTableExecutor(ABC):
    """影子表执行通道（创建 / 存在性查询）。"""

    @abstractmethod
    async def ensure_shadow_table(self, target_table: str) -> str:
        """确保影子表存在并返回其名称（已存在则幂等跳过）。"""

    @abstractmethod
    async def drop_shadow_table(self, target_table: str) -> None:
        """清理影子表（失败善后：未发布脏数据的回收，S8 回滚链路亦用）。"""


class LoggingShadowTableExecutor(ShadowTableExecutor):
    """开发/测试通道：只记录 DDL 不连目标端（无 Doris 时可观测地推进）。"""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.dropped: list[str] = []

    async def ensure_shadow_table(self, target_table: str) -> str:
        shadow = shadow_table_name(target_table)
        if shadow not in self.created:
            self.created.append(shadow)
        return shadow

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

    async def ensure_shadow_table(self, target_table: str) -> str:
        import aiomysql

        shadow = shadow_table_name(target_table)
        conn = await aiomysql.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            db=self._database,
        )
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(create_shadow_table_sql(target_table))
        finally:
            conn.close()
        return shadow

    async def drop_shadow_table(self, target_table: str) -> None:
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
                await cursor.execute(f"DROP TABLE IF EXISTS {shadow_table_name(target_table)};")
        finally:
            conn.close()
