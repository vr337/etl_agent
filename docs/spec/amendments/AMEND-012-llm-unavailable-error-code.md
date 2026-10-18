# AMEND-012：LLM 基础设施不可用错误码（SYS_503）

- 状态：**Draft（待人类审核）**
- 日期：2026-10-18
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 6.1 章 API-05/06 /
  第 8 章「性能 · LLM 生成」
- 来源：T-LLM-01（真实 LLM 对接）；`src/etl_agent/llm/errors.py`
- 处置：本追加文件**不改动 v1.0.3-Baseline 正文**；待产出 SPEC v1.0.4-Baseline 时合并到 6.4 章节

## 内容

AMEND-004 只定义了 `BIZ_422_LLM_PROVIDER_ERROR`，把「Provider 用不了」与
「这次生成的内容有问题」混在同一个 422 里。T-LLM-01 接入真实端点后，这两类必须分开：

| 段 | HTTP | 错误码 | 含义 |
|----|------|--------|------|
| SYS_ | 503 | `SYS_503_LLM_UNAVAILABLE` | LLM 基础设施不可用：请求超时 / 连接失败 / 额度耗尽（429） |

## 判定口径

| 情形 | 错误码 | HTTP | 理由 |
| --- | --- | --- | --- |
| 请求超时（`httpx.TimeoutException`） | `SYS_503_LLM_UNAVAILABLE` | 503 | 网络/上游拥塞，重试可能成功，与输入无关 |
| 连接失败（`httpx.HTTPError`） | `SYS_503_LLM_UNAVAILABLE` | 503 | 端点不可达（DNS / TLS / 拒绝连接） |
| 额度耗尽（HTTP 429） | `SYS_503_LLM_UNAVAILABLE` | 503 | 配额问题，需运维补额，不是调用方改输入能解决 |
| 其它非 2xx（4xx/5xx） | `BIZ_422_LLM_PROVIDER_ERROR` | 422 | 请求本身被拒（模型名错 / 参数非法等） |
| 响应不可解析 / 缺 `etl_plan`、`hocon` | `BIZ_422_LLM_PROVIDER_ERROR` | 422 | 模型输出不合格，属生成质量问题 |

## 红线（写入代码注释与测试）

**禁止静默降级**：`SYS_503_LLM_UNAVAILABLE` 必须原样上抛，**绝不允许**回落到
`mock_provider` 生成一份固定配置。回落会把「没生成」伪装成「生成成功」，
是本项目最不能接受的那类欺骗（与 T-QUALITY-01 的 fail-open 同源）。

## 与 AMEND-004 的关系

本文件是 AMEND-004 的**增量补充**，不修改其中既有 5 个错误码的语义：

- AMEND-004 的 `BIZ_422_LLM_PROVIDER_ERROR` 继续保留，覆盖「HTTP 错误 / 响应不可解析」；
- 本文件新增的 `SYS_503_LLM_UNAVAILABLE` 覆盖「超时 / 连接 / 额度」三类；
- `LLMProviderUnavailable` 在类继承上 **是** `LLMProviderError` 的子类，
  便于既有 `except LLMProviderError` 调用方保持兼容，但 `code` / `http_status` 为 503。

## 影响面

- `src/etl_agent/llm/errors.py` — 新增 `LLMProviderUnavailable`
- `src/etl_agent/llm/openai_provider.py` — 超时 / 连接 / 429 改抛 503
- 既有测试 `tests/generation/test_llm_provider.py::test_llm_timeout` 同步改为断言 503

## 审核动作

- 人类确认后，本文件状态改为 **Accepted**；
- 未确认前，代码按 Draft 实现，回滚点为 revert `feat/T-LLM-01`。
