"""M-F 生成引擎 — Mock Provider（测试与无 Key 环境）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 6.4 章错误码
覆盖 FR：FR-18（候选生成的可测替代路径）、FR-67（不落 Prompt / 候选正文）
对应 AC：AC-03（状态机流转）、AC-04（门禁 + 有限修复）

约束（S5 提示词 11.2 / 第十八节）：
1. **仅测试用**：生产环境（ENV=prod）调用即拒绝，防止固定响应顶替真实生成；
2. 默认返回**与传入画像自洽**的合规候选（来源/目标连接与表取自画像，
   目标列取自目标画像），因此天然通过 14 项门禁 —— 用于验证状态机 happy path；
3. 可通过 `plan_factory` / `responses` 注入不合规候选，用于验证门禁拦截与有限修复预算。
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from etl_agent.config import Settings
from etl_agent.execution.shadow import ERROR_SUFFIX, SHADOW_SUFFIX
from etl_agent.llm.errors import MockProviderForbidden
from etl_agent.llm.provider import GeneratedCandidate

logger = logging.getLogger(__name__)

PlanFactory = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]

DEFAULT_BACKEND = "mock"
DEFAULT_MODEL = "mock-etl-plan-v1"

# 质量规则（T-SHADOW-01）：字段级清洗规则 + 错误码标签（FR-22 / SPEC 3.2 M-K）。
# 三条规则对齐演示 seed 的脏行设计（infra/e2e/mysql-init/01-seed.sql:69-80）：
# ① PK 非空——**防御性检查**（PK 理论非 NULL，非当前脏行覆盖目标）；
# ② 金额非空——覆盖脏行 9001（amount NULL）；
# ③ 水位不越界——覆盖脏行 9002（updated_at 为未来时间）。
# **顺序即 CASE 优先级（首命中）**：error 分支按此顺序取 error_code。
# `code` 是 PRD 既有字段（错误码分布按 `rules[].code` 聚合，PRD 第 10 章）。
#
# 每条规则带**两个表达式**：
# - `pass`（规则本体，写进契约 predicates / valid 分支）——`X IS NOT NULL`、`X <= Y`；
# - `fail`（直接否定，写进 error 分支）——`X IS NULL`、`X > Y`。
# 不能写成 `not (pass)`：SeaTunnel 的 Sql transform **不支持 `NOT (x IS NOT NULL)`**
# （`COMMON-05 Unsupported SQL Expression`，run22 实证）；直接否定形式可用。
_QUALITY_RULES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "not_null_primary_key",
        "{pk} IS NOT NULL",
        "{pk} IS NULL",
        "QUALITY_NOT_NULL_PK",
        "{pk} 为空",
    ),
    (
        "not_null_amount",
        "amount IS NOT NULL",
        "amount IS NULL",
        "QUALITY_NOT_NULL_AMOUNT",
        "amount 为空",
    ),
    (
        "watermark_not_future",
        "updated_at <= CURRENT_TIMESTAMP",
        "updated_at > CURRENT_TIMESTAMP",
        "QUALITY_FUTURE_WATERMARK",
        "updated_at 超出水位上界（未来时间）",
    ),
)


def _quality_rules(source_columns: list[str]) -> list[dict[str, str]]:
    """质量规则列表（name / pass / fail / code / reason）；PK 列取来源画像首列。"""
    pk = source_columns[0] if source_columns else "id"
    return [
        {
            "name": name,
            "pass": pass_expression.format(pk=pk),
            "fail": fail_expression.format(pk=pk),
            "code": code,
            "reason": reason.format(pk=pk),
        }
        for name, pass_expression, fail_expression, code, reason in _QUALITY_RULES
    ]


def _data_plane_tables(target_table: str) -> tuple[str, str]:
    """数据面两表名（有效 → 影子表；脏数据 → 错误表）。

    与 `execution.shadow` 的派生名**同源**（直接复用其后缀常量）——这是 T-SHADOW-02
    的修法：契约 `valid_sink` 与计划推得的影子表名不再各说各话。
    """
    return f"{target_table}{SHADOW_SUFFIX}", f"{target_table}{ERROR_SUFFIX}"


def compliant_plan(
    requirement: str, source_profile: dict[str, Any], sink_profile: dict[str, Any]
) -> dict[str, Any]:
    """构造与画像自洽的合规 EtlPlan（质量契约四要素齐备，无明文凭据）。"""
    source_columns = [column["name"] for column in source_profile.get("columns", [])]
    rules = _quality_rules(source_columns)
    table = str(sink_profile.get("table_name"))
    shadow, error = _data_plane_tables(table)
    return {
        "pipeline_name": "mock_pipeline",
        "backend": "direct",
        "source": {
            "connector_type": source_profile.get("connector_type"),
            "connection_id": source_profile.get("connection_id"),
            "table": source_profile.get("table_name"),
        },
        "sink": {
            "connector_type": sink_profile.get("connector_type"),
            "connection_id": sink_profile.get("connection_id"),
            "table": sink_profile.get("table_name"),
            "target_schema": {
                "columns": [
                    {"name": column["name"], "type": column["type"]}
                    for column in sink_profile.get("columns", [])
                ]
            },
        },
        "transforms": [
            {
                "type": "sql",
                "sql": f"select {', '.join(source_columns) or '*'} from {source_profile.get('table_name')}",
            }
        ],
        "quality_contract": {
            # T-SHADOW-01/T-SHADOW-02：命名统一到 `<目标表>_shadow` / `<目标表>_error`
            # （与 execution.shadow 的派生名同源，消除契约与计划的漂移）
            "valid_sink": shadow,
            "error_sink": error,
            # T-QUALITY-01 / fix-final-gate：**必须显式声明拒绝率阈值**。
            # 缺了它 `classify_quality` 的两个超限判据（`max_rejected_rows` /
            # `max_reject_rate`）同时不成立 → 直接落 PASSED，即 **fail-open**：
            # 哪怕拒绝率 100% 也判通过、发布门禁第 2 道永远放行（追踪见 tracking.md）。
            # 0.05 = 5%：演示 seed 的 3/1003 ≈ 0.30% 稳稳落在阈值内（仍 PASSED，
            # 取证数字不变），而一旦拒绝率真超 5% 就 FAILED 并阻断发布。
            "max_reject_rate": 0.05,
            "routing": [
                *({"rule": rule["name"], "sink": shadow} for rule in rules),
                {"rule": "default", "sink": error},
            ],
            "traceability": {
                "source_row_id_column": source_columns[0] if source_columns else "id",
                "error_code_column": "error_code",
                "reason_column": "error_reason",
            },
            # 每条规则带 `code` 标签——错误码分布按 `rules[].code` 聚合（PRD 第 10 章）。
            # `expression` 取**通过态**（规则本体）；否定态只在 HOCON 的 error 分支用。
            "predicates": [
                {"name": rule["name"], "expression": rule["pass"], "code": rule["code"]}
                for rule in rules
            ],
        },
    }


# T-ENGINE-02：连接器类型 → (JDBC URL 前缀, driver class)。
# Doris FE 讲 MySQL 协议（9030 与 MySQL 同端口探查/写入），故 DORIS 与 MYSQL 同 driver；
# MYSQL/DORIS 所需的 mysql-connector-j 已在引擎 lib/（实测），worker 白名单含 connector-jdbc。
# TODO(T-ENGINE-02)：未覆盖类型（ORACLE/SQL_SERVER/HIVE/HDFS/LOCAL_FILE）暂回落 POSTGRESQL
# 并打 warning——按演示所需最小集，不过度通用化；需要时在此表追加一行。
_JDBC_CONNECTORS: dict[str, tuple[str, str]] = {
    "MYSQL": ("jdbc:mysql://", "com.mysql.cj.jdbc.Driver"),
    "DORIS": ("jdbc:mysql://", "com.mysql.cj.jdbc.Driver"),
    "POSTGRESQL": ("jdbc:postgresql://", "org.postgresql.Driver"),
    # S11 T-21：ClickHouse 走官方 JDBC（HTTP 8123 / native 9000 由驱动自行选择）。
    # 注意：引擎侧需 clickhouse-jdbc 与依赖包在 lib/，真跑 ClickHouse 前须核对
    # worker 镜像插件索引（T-ENGINE-02 教训：驱动缺失只在真跑时炸）。
    "CLICKHOUSE": ("jdbc:clickhouse://", "com.clickhouse.jdbc.ClickHouseDriver"),
}
_DEFAULT_JDBC = _JDBC_CONNECTORS["POSTGRESQL"]


def _jdbc_target(profile: dict[str, Any]) -> tuple[str, str]:
    """按 profile 的 connector_type 解析 (URL 前缀, driver class)。

    未覆盖类型：回落 POSTGRESQL 并 warning（不静默——T-ENGINE-01 真跑时
    「静默用错 driver」正是缺陷 1 的教训）。
    """
    connector = str(profile.get("connector_type") or "").upper()
    target = _JDBC_CONNECTORS.get(connector)
    if target is None:
        logger.warning(
            "HOCON 渲染：connector_type=%r 未被 _JDBC_CONNECTORS 覆盖，回落 POSTGRESQL"
            "（T-ENGINE-02 TODO：按需扩充映射表，勿静默）",
            connector or "<empty>",
        )
        return _DEFAULT_JDBC
    return target


def _jdbc_block(profile: dict[str, Any], prefix: str, tail: str) -> str:
    """渲染一个 Jdbc 块（driver/URL 按连接器类型，凭据仍走占位符，FR-68）。

    用字符串拼接而非 f-string：`${VAR}` 字面量在 f-string 里需双花括号转义，
    易错（本次首版即踩坑）；拼接形态与占位符契约一一对应，直读。

    `tail` 是连接器特有配置行：source 传 `query`（读表），sink 传 `table` 组
    （T-ENGINE-03：由连接器按目标表元数据生成 INSERT）。
    """
    url_prefix, driver = _jdbc_target(profile)
    return (
        "  Jdbc {\n"
        '    url = "' + url_prefix + "${" + prefix + "_HOST}:${" + prefix + "_PORT}/${" + prefix + '_DB}"\n'
        '    driver = "' + driver + '"\n'
        '    user = "${' + prefix + '_USER}"\n'
        '    password = "${' + prefix + '_PASSWORD}"\n'
        + tail
        + "  }\n"
    )


def _sql_transform(plugin_input: str, plugin_output: str, query: str) -> str:
    """渲染一个 `Sql` transform 块（T-SHADOW-01 双路分流的路由节点）。

    `plugin_input` / `plugin_output` 是 SeaTunnel 的流标签（实测 2.3.13 生效）：
    上游的 `plugin_output` 即下游 SQL 里的表名，下游用 `plugin_input` 声明消费哪一路。
    """
    return (
        "  Sql {\n"
        '    plugin_input = "' + plugin_input + '"\n'
        '    plugin_output = "' + plugin_output + '"\n'
        '    query = "' + query + '"\n'
        "  }\n"
    )


def _sink_tail(plugin_input: str, table: str, database: str) -> str:
    """渲染 sink 的特有配置行（T-ENGINE-03 四选项 + T-SHADOW-01 分流标签）。

    T-ENGINE-03 四选项缺一不可（真引擎逐级实测）——缺 `generate_sink_sql` →
    SeaTunnel 仍要求 `query`；缺 `database` → 校验失败；`enable_upsert` 默认 true →
    追加 `ON DUPLICATE KEY UPDATE`，Doris 不支持该语法。
    """
    return (
        '    plugin_input = "' + plugin_input + '"\n'
        '    table = "' + table + '"\n'
        '    database = "' + database + '"\n'
        "    generate_sink_sql = true\n"
        "    enable_upsert = false\n"
    )


def compliant_hocon(source_profile: dict[str, Any], sink_profile: dict[str, Any]) -> str:
    """构造合规 HOCON：凭据一律 `${ENV}` 占位符（FR-68：图内只传占位符）。

    T-ENGINE-02：driver / URL 按 profile 的 `connector_type` 渲染
    （MYSQL / DORIS → `jdbc:mysql://` + `com.mysql.cj.jdbc.Driver`；
    POSTGRESQL 保持原形态），真实引擎才连得上源与目标。

    T-ENGINE-03：sink 由 `query` 改 `table` 形态。SeaTunnel 的 `query` 模式把 SQL
    **原样**作为 PreparedStatement 下发、不补 VALUES 子句，`insert into <表>` 因此
    被 Doris 解析器拒绝（`mismatched input ';' ...`）；`table` 形态由连接器按目标表
    元数据生成完整 INSERT（列清单按列名映射，免疫列序变化）。
    旧 `query` 形态已在 v1.0.3 起弃用（见下方 sink 注释），勿回退。

    T-SHADOW-01：**双路分流**（FR-44）——source 打 `plugin_output = "src"` 标签，
    两个 `Sql` transform 按质量契约谓词把数据切成 valid / error 两路，两个 `Jdbc` sink
    用 `plugin_input` 分别消费：有效数据 → `<目标表>_shadow`，脏数据 → `<目标表>_error`
    （错误表带 `error_code` / `error_reason`，PRD 10.5 #4 例外条款允许保留原始值）。
    error 路的 `error_code` 用 **CASE 按规则声明顺序取首命中**（一行恰好一条错误记录，
    拒绝率不被重复计数污染），而非每条规则各写一次。
    """
    sink_table = str(sink_profile.get("table_name"))
    shadow_table, error_table = _data_plane_tables(sink_table)
    sink_database = str(sink_profile.get("schema_name"))
    source_columns = [str(column.get("name")) for column in source_profile.get("columns", [])]
    projection = ", ".join(source_columns) or "*"
    rules = _quality_rules(source_columns)
    valid_condition = " and ".join(f"({rule['pass']})" for rule in rules)
    # error 分支用**直接否定态**（`X IS NULL` / `X > Y`），不用 `not (pass)`——
    # SeaTunnel Sql transform 不支持 `NOT (x IS NOT NULL)`（run22 实证
    # `COMMON-05 Unsupported SQL Expression`）。
    error_condition = " or ".join(f"({rule['fail']})" for rule in rules)
    code_case = " ".join(f"when ({rule['fail']}) then '{rule['code']}'" for rule in rules)
    reason_case = " ".join(f"when ({rule['fail']}) then '{rule['reason']}'" for rule in rules)
    error_code_expr = f"case {code_case} else 'QUALITY_UNKNOWN' end"
    error_reason_expr = f"case {reason_case} else '命中质量规则（未归类）' end"
    return (
        "env {\n"
        '  job.mode = "BATCH"\n'
        "}\n"
        "\n"
        "source {\n"
        + _jdbc_block(
            source_profile,
            "SRC",
            '    query = "select * from ' + str(source_profile.get("table_name")) + '"\n'
            '    plugin_output = "src"\n',
        )
        + "}\n"
        "\n"
        "transform {\n"
        + _sql_transform("src", "valid", f"select {projection} from src where {valid_condition}")
        + _sql_transform(
            "src",
            "error",
            f"select {projection}, {error_code_expr} as error_code, "
            f"{error_reason_expr} as error_reason from src where {error_condition}",
        )
        + "}\n"
        "\n"
        "sink {\n"
        # T-ENGINE-03：旧 `query` 形态（SeaTunnel query 模式原样下发不补 VALUES，Doris 解析必败）
        + _jdbc_block(sink_profile, "SINK", _sink_tail("valid", shadow_table, sink_database))
        + _jdbc_block(sink_profile, "SINK", _sink_tail("error", error_table, sink_database))
        + "}\n"
    )


class MockProvider:
    """固定响应的 Provider（S5 测试与无 Key 环境）。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        plan_factory: PlanFactory | None = None,
        hocon_factory: Callable[[dict[str, Any], dict[str, Any]], str] | None = None,
        responses: Sequence[GeneratedCandidate] | None = None,
        backend: str = DEFAULT_BACKEND,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._settings = settings
        self._plan_factory = plan_factory or compliant_plan
        self._hocon_factory = hocon_factory or compliant_hocon
        self._responses = list(responses or [])
        self._backend = backend
        self._model = model
        self.calls = 0

    async def generate_etl_plan(
        self,
        requirement: str,
        source_profile: dict[str, Any],
        sink_profile: dict[str, Any],
        feedback: str | None = None,
    ) -> GeneratedCandidate:
        """返回脚本化响应（若有）或按画像生成的合规候选。

        Raises:
            MockProviderForbidden: 生产环境启用 mock（S5 提示词 11.2）。
        """
        # 生产禁止 mock：固定响应会在生产冒充真实生成能力（11.2「生产拒绝启用」）
        if self._settings is not None and self._settings.is_production:
            raise MockProviderForbidden("生产环境禁止使用 mock LLM Provider")
        self.calls += 1
        if self._responses:
            index = min(self.calls - 1, len(self._responses) - 1)
            return self._responses[index]
        return GeneratedCandidate(
            etl_plan=self._plan_factory(requirement, source_profile, sink_profile),
            hocon=self._hocon_factory(source_profile, sink_profile),
            backend=self._backend,
            model=self._model,
        )
