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
from etl_agent.llm.errors import MockProviderForbidden
from etl_agent.llm.provider import GeneratedCandidate

logger = logging.getLogger(__name__)

PlanFactory = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]

DEFAULT_BACKEND = "mock"
DEFAULT_MODEL = "mock-etl-plan-v1"


def compliant_plan(
    requirement: str, source_profile: dict[str, Any], sink_profile: dict[str, Any]
) -> dict[str, Any]:
    """构造与画像自洽的合规 EtlPlan（质量契约四要素齐备，无明文凭据）。"""
    source_columns = [column["name"] for column in source_profile.get("columns", [])]
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
            "valid_sink": f"{sink_profile.get('table_name')}_valid",
            "error_sink": f"{sink_profile.get('table_name')}_error",
            "routing": [
                {"rule": "not_null_primary_key", "sink": f"{sink_profile.get('table_name')}_valid"},
                {"rule": "default", "sink": f"{sink_profile.get('table_name')}_error"},
            ],
            "traceability": {
                "source_row_id_column": source_columns[0] if source_columns else "id",
                "error_code_column": "error_code",
                "reason_column": "error_reason",
            },
            "predicates": [
                {"name": "not_null_primary_key", "expression": "id IS NOT NULL"},
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


def _jdbc_block(profile: dict[str, Any], prefix: str, query: str) -> str:
    """渲染一个 Jdbc 块（driver/URL 按连接器类型，凭据仍走占位符，FR-68）。

    用字符串拼接而非 f-string：`${VAR}` 字面量在 f-string 里需双花括号转义，
    易错（本次首版即踩坑）；拼接形态与占位符契约一一对应，直读。
    """
    url_prefix, driver = _jdbc_target(profile)
    return (
        "  Jdbc {\n"
        '    url = "' + url_prefix + "${" + prefix + "_HOST}:${" + prefix + "_PORT}/${" + prefix + '_DB}"\n'
        '    driver = "' + driver + '"\n'
        '    user = "${' + prefix + '_USER}"\n'
        '    password = "${' + prefix + '_PASSWORD}"\n'
        '    query = "' + query + '"\n'
        "  }\n"
    )


def compliant_hocon(source_profile: dict[str, Any], sink_profile: dict[str, Any]) -> str:
    """构造合规 HOCON：凭据一律 `${ENV}` 占位符（FR-68：图内只传占位符）。

    T-ENGINE-02：driver / URL 按 profile 的 `connector_type` 渲染
    （MYSQL / DORIS → `jdbc:mysql://` + `com.mysql.cj.jdbc.Driver`；
    POSTGRESQL 保持原形态），真实引擎才连得上源与目标。
    """
    return (
        "env {\n"
        '  job.mode = "BATCH"\n'
        "}\n"
        "\n"
        "source {\n"
        + _jdbc_block(source_profile, "SRC", f"select * from {source_profile.get('table_name')}")
        + "}\n"
        "\n"
        "sink {\n"
        + _jdbc_block(sink_profile, "SINK", f"insert into {sink_profile.get('table_name')}")
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
