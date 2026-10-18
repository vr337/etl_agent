"""M-F 生成引擎 — OpenAI 兼容 Provider（httpx 异步，多供应商共用）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 2.2 章第三方依赖（OpenAI 兼容多供应商）/ 第 8 章「性能 · LLM 生成」
覆盖 FR：FR-18（EtlPlan / HOCON 候选生成）、FR-67（Prompt 与候选正文不落业务库）
对应 AC：AC-02（模型零凭据）、AC-04（候选交门禁裁决）

四条红线（T-LLM-01）：
1. 统一超时 `ETL_AGENT_LLM_TIMEOUT_SECONDS`（**默认 120s**）；
2. **不记 Prompt**：本模块不写任何日志、不落任何库，requirement 只出现在请求体中；
3. 失败分级：超时 / 连接 / 额度耗尽 → `LLMProviderUnavailable`（503）；
   HTTP 错误 / 响应不可解析 → `LLMProviderError`（422）。**绝不回落 mock**；
4. 结构化约束：首选 `response_format={"type": "json_object"}`；
   平台不支持时**协议降级**（去掉该字段重发一次），不是静默降级 —— 仍会正常报错。

Prompt 工程（T-LLM-01 核心）：System Prompt 承担四件事 —— 角色定位、输出契约、
安全红线、**few-shot 示例**；门禁反馈走 user 消息的 `gate_feedback` 字段，
由 `gate.service.build_repair_feedback` 构造（只含规则 ID + 脱敏摘要，不含 HOCON 正文）。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from etl_agent.config import Settings
from etl_agent.llm.errors import LLMProviderError, LLMProviderUnavailable
from etl_agent.llm.provider import GeneratedCandidate

# 失败分类（遥测只记分类名，不记正文，FR-67）
FAILURE_TIMEOUT = "LLM_TIMEOUT"
FAILURE_CONNECT = "LLM_CONNECT_ERROR"
FAILURE_RATE_LIMIT = "LLM_RATE_LIMITED"
FAILURE_HTTP = "LLM_HTTP_ERROR"
FAILURE_PAYLOAD = "LLM_BAD_PAYLOAD"

# 400 响应体里出现这些字样 → 判定为「平台不支持 response_format」，走协议降级
_RESPONSE_FORMAT_HINTS = ("response_format", "json_object", "unsupported", "not support")

# few-shot 用 Python 结构承载，由 json.dumps 序列化 —— 保证示例本身是合法 JSON
_FEW_SHOT_PLAN: dict[str, Any] = {
    "pipeline_name": "orders_to_order_wide",
    "backend": "direct",
    "source": {
        "connector_type": "MYSQL",
        "connection_id": "<源连接ID>",
        "table": "orders",
    },
    "sink": {
        "connector_type": "DORIS",
        "connection_id": "<目标连接ID>",
        "table": "order_wide",
        "target_schema": {
            "columns": [
                {"name": "order_id", "type": "bigint"},
                {"name": "amount", "type": "decimal(18,2)"},
            ]
        },
    },
    "transforms": [{"type": "sql", "sql": "select order_id, amount from orders"}],
    "quality_contract": {
        "valid_sink": "order_wide_shadow",
        "error_sink": "order_wide_error",
        "max_reject_rate": 0.05,
        "routing": [
            {"rule": "not_null_order_id", "sink": "order_wide_shadow"},
            {"rule": "default", "sink": "order_wide_error"},
        ],
        "traceability": {
            "source_row_id_column": "order_id",
            "error_code_column": "error_code",
            "reason_column": "error_reason",
        },
        "predicates": [
            {
                "name": "not_null_order_id",
                "expression": "order_id IS NOT NULL",
                "code": "QUALITY_NOT_NULL_PK",
            }
        ],
    },
}

_FEW_SHOT_HOCON = """env {
  job.mode = "BATCH"
}
source {
  Jdbc {
    driver = "com.mysql.cj.jdbc.Driver"
    url = "jdbc:mysql://${SRC_HOST}:${SRC_PORT}/${SRC_DB}"
    user = "${SRC_USER}"
    password = "${SRC_PASSWORD}"
    query = "select * from orders"
    plugin_output = "src"
  }
}
transform {
  Sql {
    plugin_input = "src"
    plugin_output = "valid"
    query = "select order_id, amount from src where order_id IS NOT NULL"
  }
  Sql {
    plugin_input = "src"
    plugin_output = "error"
    query = "select order_id, amount, 'QUALITY_NOT_NULL_PK' as error_code, 'order_id 为空' as error_reason from src where order_id IS NULL"
  }
}
sink {
  Jdbc {
    plugin_input = "valid"
    driver = "com.mysql.cj.jdbc.Driver"
    url = "jdbc:mysql://${SINK_HOST}:${SINK_PORT}/${SINK_DB}"
    user = "${SINK_USER}"
    password = "${SINK_PASSWORD}"
    table = "order_wide_shadow"
    database = "${SINK_DB}"
    generate_sink_sql = true
    enable_upsert = false
  }
  Jdbc {
    plugin_input = "error"
    driver = "com.mysql.cj.jdbc.Driver"
    url = "jdbc:mysql://${SINK_HOST}:${SINK_PORT}/${SINK_DB}"
    user = "${SINK_USER}"
    password = "${SINK_PASSWORD}"
    table = "order_wide_error"
    database = "${SINK_DB}"
    generate_sink_sql = true
    enable_upsert = false
  }
}
"""

_FEW_SHOT_JSON = json.dumps(
    {"etl_plan": _FEW_SHOT_PLAN, "hocon": _FEW_SHOT_HOCON},
    ensure_ascii=False,
    indent=2,
)

SYSTEM_PROMPT = (
    "你是顶级 ETL 架构师，负责把自然语言的数据集成需求翻译成 SeaTunnel 的\n"
    "EtlPlan（结构化 JSON）与 HOCON 配置。\n"
    "\n"
    "【输出契约 —— 违反即失败】\n"
    "1. 只输出一个合法 JSON 对象，形如：{\"etl_plan\": {...}, \"hocon\": \"...\"}\n"
    "2. 禁止任何解释性文字（如「好的，我为你生成…」），禁止前后缀说明。\n"
    "   若平台不支持 JSON 模式，必须把 JSON 包在 ```json 与 ``` 之间，围栏外不得有任何字符。\n"
    "3. etl_plan 必须且只能包含以下字段，缺一不可：\n"
    "   pipeline_name   : string\n"
    "   backend         : \"direct\"\n"
    "   source          : {connector_type, connection_id, table}\n"
    "   sink            : {connector_type, connection_id, table,\n"
    "                      target_schema: {columns: [{name, type}]}}\n"
    "   transforms      : [{type: \"sql\", sql: string}]\n"
    "   quality_contract: {\n"
    "       valid_sink      : string   // 必须等于 <目标表>_shadow\n"
    "       error_sink      : string   // 必须等于 <目标表>_error\n"
    "       max_reject_rate : number   // 0 < x < 1，建议 0.05\n"
    "       routing         : [{rule: string, sink: string}]  // 最后一项 rule 必须是 \"default\"\n"
    "       traceability    : {source_row_id_column, error_code_column, reason_column}\n"
    "       predicates      : [{name, expression, code}]\n"
    "   }\n"
    "4. hocon 必须是字符串，含 env / source / transform / sink 四块。\n"
    "\n"
    "【安全红线 —— 违反即失败】\n"
    "- 严禁输出任何真实口令、密钥、连接串明文。\n"
    "- 凭据位置只能写占位符：${SRC_USER} ${SRC_PASSWORD} ${SINK_USER} ${SINK_PASSWORD}\n"
    "- 不得把 profile 里的样本值、统计值写进 hocon。\n"
    "\n"
    "【HOCON 结构约定】\n"
    "- **每个 Jdbc 块都必须写 driver**（漏了会在提交时报\n"
    "  API-02 \"the options('driver') are required\"，run31 实证）。按 connector_type 取：\n"
    "    MYSQL / DORIS → com.mysql.cj.jdbc.Driver\n"
    "    POSTGRESQL    → org.postgresql.Driver\n"
    "    CLICKHOUSE    → com.clickhouse.jdbc.ClickHouseDriver\n"
    "- source 用 Jdbc 块，带 plugin_output = \"src\"\n"
    "- transform 用两个 Sql 块：valid（质量通过）与 error（质量不通过），\n"
    "  各自 plugin_input = \"src\"、plugin_output = \"valid\" / \"error\"\n"
    "- sink 用两个 Jdbc 块：plugin_input = \"valid\" → <目标表>_shadow；\n"
    "  plugin_input = \"error\" → <目标表>_error（error 路多两列 error_code / error_reason）\n"
    "- **sink 块必须同时写齐四选项，缺一即提交失败**（T-ENGINE-03 真引擎逐级实测）：\n"
    "    table              = \"<目标表>\"\n"
    "    database           = \"${SINK_DB}\"\n"
    "    generate_sink_sql  = true    // 缺它 → API-02 the options('query') are required\n"
    "    enable_upsert      = false   // 缺它默认 true → 追加 ON DUPLICATE KEY UPDATE，Doris 不支持\n"
    "- error 分支必须写直接否定态（X IS NULL、X > CURRENT_TIMESTAMP），\n"
    "  禁止写 not (X IS NOT NULL) —— SeaTunnel 不支持该表达式。\n"
    "\n"
    "【示例】输入 requirement=\"把 orders 同步到 order_wide\" 时，输出应为：\n" + _FEW_SHOT_JSON + "\n"
    "\n"
    "【修复模式】\n"
    "当 user 消息中的 gate_feedback 非空时，表示上一版候选被 14 项确定性门禁拒绝。\n"
    "逐条修复 gate_feedback 列出的问题，保留其余部分不变，重新输出完整 JSON。\n"
)


class OpenAICompatibleProvider:
    """OpenAI 兼容端点 Provider（openai / deepseek / doubao / kimi / zhipu / custom 共用）。"""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        # 传输层可注入：单测用 MockTransport 复现超时 / HTTP 错误 / 坏载荷三类失败
        self._transport = transport

    @property
    def model(self) -> str:
        return self._settings.llm_model

    async def generate_etl_plan(
        self,
        requirement: str,
        source_profile: dict[str, Any],
        sink_profile: dict[str, Any],
        feedback: str | None = None,
    ) -> GeneratedCandidate:
        """调用 `/chat/completions` 生成候选。

        Raises:
            LLMProviderUnavailable: 超时 / 连接失败 / 额度耗尽（429）—— 503，**不回落 mock**。
            LLMProviderError:       非 2xx（429 以外）或响应体不可解析为
                `{etl_plan, hocon}` —— 422。message 只含分类与状态码，不含 Prompt 正文。
        """
        body = await self._post(self._build_payload(requirement, source_profile, sink_profile, feedback))
        return self._parse(body)

    def _build_payload(
        self,
        requirement: str,
        source_profile: dict[str, Any],
        sink_profile: dict[str, Any],
        feedback: str | None,
    ) -> dict[str, Any]:
        """组装请求体：system（含 few-shot）+ user（需求 / 双画像 / 门禁反馈）。"""
        return {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "requirement": requirement,
                            "source_profile": source_profile,
                            "sink_profile": sink_profile,
                            "gate_feedback": feedback,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            # 结构化输出强约束（T-LLM-01）：平台不支持时由 _post 协议降级
            "response_format": {"type": "json_object"},
        }

    async def _post(self, payload: dict[str, Any]) -> str:
        """发请求并返回响应正文（失败按分类抛错）。"""
        url = f"{self._settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.llm_timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
                if self._should_retry_without_json_mode(response):
                    # 协议降级（**不是**静默降级）：平台不支持 response_format，
                    # 去掉该字段重发一次；若仍失败，照常按 HTTP 错误报出，绝不回落 mock。
                    degraded = {
                        key: value for key, value in payload.items() if key != "response_format"
                    }
                    response = await client.post(url, json=degraded, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMProviderUnavailable(f"{FAILURE_TIMEOUT}: 生成请求超时") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderUnavailable(f"{FAILURE_CONNECT}: {type(exc).__name__}") from exc

        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise LLMProviderUnavailable(f"{FAILURE_RATE_LIMIT}: status=429")
        if response.status_code >= 400:
            raise LLMProviderError(f"{FAILURE_HTTP}: status={response.status_code}")
        return response.text

    @staticmethod
    def _should_retry_without_json_mode(response: httpx.Response) -> bool:
        """400 且响应体提示不支持 `response_format` → 判定为需要协议降级。"""
        if response.status_code != httpx.codes.BAD_REQUEST:
            return False
        body = response.text.lower()
        return any(hint in body for hint in _RESPONSE_FORMAT_HINTS)

    def _parse(self, body: str) -> GeneratedCandidate:
        """解析响应：content 必须是含 `etl_plan` + `hocon` 的 JSON 对象。

        严格口径（T-LLM-01）：只做**提取**（剥 ```json 围栏、定位首个 `{` 到末个 `}`），
        缺字段或类型不符一律抛错 —— **禁止**用正则或默认值「补」出缺失字段。
        """
        try:
            envelope = json.loads(body)
            content = envelope["choices"][0]["message"]["content"]
            data = _load_json_object(content)
            plan = data["etl_plan"]
            hocon = data["hocon"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError(f"{FAILURE_PAYLOAD}: 响应不可解析为 etl_plan + hocon") from exc
        if not isinstance(plan, dict) or not isinstance(hocon, str) or not hocon.strip():
            raise LLMProviderError(f"{FAILURE_PAYLOAD}: etl_plan 必须是对象且 hocon 非空")
        return GeneratedCandidate(
            etl_plan=plan,
            hocon=hocon,
            backend="openai-compatible",
            model=self._settings.llm_model,
        )


def _load_json_object(content: str) -> dict[str, Any]:
    """从模型输出中提取 JSON 对象（只剥围栏与定位边界，不改写内容）。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object in content")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise TypeError("json root is not an object")
    return parsed
