# AMEND-004：生成域错误码补充

- 状态：Accepted
- 日期：2026-10-14
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 6.1 章 API-05/06/07
- 来源：S5 阶段报告（DoD-17 / 第十九节类型 C）；`src/etl_agent/generation/errors.py` 与 `src/etl_agent/llm/errors.py` 共 5 个新错误码
- 处置：本追加文件**不改动 v1.0.3-Baseline 正文**；待产出 SPEC v1.0.4-Baseline 时合并到 6.4 章节

## 内容

SPEC 6.4 未定义生成域（API-05 创建运行 / API-06 澄清恢复）的运行态与 Provider 错误码。S5 实现时新增：

| 段 | HTTP | 错误码 | 含义 |
|----|------|--------|------|
| BIZ_ | 404 | `BIZ_404_AGENT_RUN_NOT_FOUND` | 生成运行不存在（跨项目视同不存在，不泄露存在性，SPEC 6.4 BIZ_404 语义） |
| BIZ_ | 409 | `BIZ_409_AGENT_RUN_STATE_CONFLICT` | 运行状态机不允许当前操作（如已终态的运行再次澄清） |
| BIZ_ | 422 | `BIZ_422_LLM_PROVIDER_ERROR` | LLM Provider 调用失败（超时 / HTTP 错误 / 响应不可解析；摘要只含失败分类，不含 Prompt） |
| BIZ_ | 422 | `BIZ_422_LLM_BUDGET_EXHAUSTED` | 候选预算耗尽（FR-21：含初始候选最多 3 份，耗尽即确定性失败） |
| BIZ_ | 422 | `BIZ_422_INTERRUPT_NOT_PENDING` | 非中断态收到澄清回答（FR-17：仅 WAITING_INPUT 可恢复） |

## 复用（非新增，不计入本追加）

| 段 | HTTP | 错误码 | 用途 |
|----|------|--------|------|
| BIZ_ | 422 | `BIZ_422_GATE_FAILED` | 确定性门禁未通过（SPEC 6.4 已定义，S5 复用，附脱敏失败摘要） |
| BIZ_ | 404 | `BIZ_404_NOT_FOUND` | Pipeline 版本不存在（SPEC 6.4 通用码，S5 复用） |
| BIZ_ | 409 | `BIZ_409_STATE_CONFLICT` | 版本状态不允许生成 / 版本尚未通过门禁冻结（SPEC 6.4） |
| BIZ_ | 400 | `BIZ_400_VALIDATION` | 缺少 Profile ID 等参数校验失败（SPEC 6.4） |
| BIZ_ | 404 | `BIZ_404_PROFILE_NOT_FOUND` | 生成引用的 Profile 不存在或跨项目（AMEND-003） |
| BIZ_ | 404 | `BIZ_404_FILE_ASSET_NOT_FOUND` | 生成引用的文件资产不存在或跨项目（AMEND-003） |
| AUTH_ | 401/403 | `AUTH_401_UNKNOWN_ACTOR` / `AUTH_403_NOT_PROJECT_MEMBER` | 身份未知 / 非项目成员（SPEC 6.4 / S2） |

## 与 S5 提示词的两处口径差异（需人类裁决）

1. **API-05 出参状态**：提示词 §11.9 写 `status: "QUEUED"`，而 AC-03 要求「运行 Agent 时即进入 WAITING_INPUT 并挂起澄清问题」。
   实现取**推进后的真实状态**（`WAITING_INPUT` / `WAITING_APPROVAL` / `FAILED`）：若固定返回 `QUEUED`，
   创建调用将无法观察到澄清挂起，AC-03 在 API 层不可验证。S5 无 Celery Worker（S7 起），
   生成在请求内同步推进，故不存在「返回 QUEUED 后由后台推进」的窗口。
2. **API-05 入参 `pipeline_version_id`**：提示词 §11.9 的 Body 未列该字段，但 `agent_runs.pipeline_version_id`
   是 NOT NULL 外键（SPEC 5.2），且 SPEC 6.2 API-05 入参明确包含它。
   实现取**兼容**：该字段可选，缺省时自动创建 Pipeline + 首个版本（名称不含需求文本，避免 Prompt 落业务库）。
3. **预算耗尽的对外形态**：提示词 §11.8 要求返回 `BIZ_422_LLM_BUDGET_EXHAUSTED`（422），
   而 §11.9 API-06 出参又允许 `status: FAILED`。实现取**两者并存**：运行先置 `FAILED` 并落遥测（可审计），
   再对外抛 422；避免出现「运行已失败但调用方收到成功响应」的语义空洞。

## 处理

- 在此之前，S5+ 阶段的错误码清单 = SPEC 6.4 + AMEND-001（Bootstrap）+ AMEND-002（连接域）+ AMEND-003（探查域 / 文件资产域）+ **本追加文件（生成域）**；
- 审核动作：人类确认后 `git add docs/spec/amendments/AMEND-004-generation-error-codes.md` + `git commit`，状态改为 Accepted；
- **本草稿不随 S5 提交自动入库**（S5 禁止事项 19：AMEND 草稿由人类审核后手动提交）。

## 口径裁决（S5 收尾确认）

1. API-05 返回**推进后的真实状态**（WAITING_INPUT / WAITING_APPROVAL / FAILED），不是固定 QUEUED；
   理由：S5 无 Celery Worker，同步推进，固定 QUEUED 会使 AC-03 在 API 层不可验证。
2. API-05 入参 `pipeline_version_id` **可选**；缺省时自动创建 Pipeline + 首个版本（名称不含需求文本）；
   理由：`agent_runs.pipeline_version_id` 为 NOT NULL 外键（SPEC 5.2）。
3. 预算耗尽**先置 FAILED + 遥测，再抛 422**；
   理由：避免"运行已失败但调用方收到成功响应"的语义空洞。
