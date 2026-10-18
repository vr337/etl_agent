/**
 * M-P 前端控制台 — 与后端 schemas 对齐的类型定义。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.2 章接口契约 / 第 6.4 章错误码
 * 覆盖 FR：FR-53~58
 * 对应 AC：AC-10、AC-17
 *
 * 口径：字段名与各模块 `schemas.py` 的 Pydantic 模型逐一对齐，
 * 前端不做重命名（避免两套词汇表）。日期一律 ISO 字符串（后端 FastAPI 默认序列化）。
 */

export type RiskLevel = 'P0' | 'P1' | 'P2' | 'P3'

export type Decision = 'ALLOW' | 'DENY' | 'REQUIRE_APPROVAL' | 'DEFER'

/* ---- Auth / 工作区 ---- */

export interface BootstrapRequest {
  token: string
  project_name: string
  admin_subject: string
}

/**
 * Bootstrap 响应（对齐后端 `BootstrapResponse`：只有 `project_id` / `admin_user_id`）。
 *
 * 曾多声明 `project_name` / `created` —— 后端不返回。
 */
export interface BootstrapResponse {
  project_id: string
  admin_user_id: string
}

/** 项目成员（对齐后端 `MemberResponse`：含 `display_name`，无 `created_at`）。 */
export interface MemberView {
  user_id: string
  subject: string
  display_name: string
  role: string
}

/** 角色授予（对齐后端 `RoleGrantResponse`；时间字段叫 `created_at`，非 `granted_at`）。 */
export interface RoleGrantView {
  id: string
  user_id: string
  subject: string
  role: string
  granted_by: string | null
  created_at: string
}

/* ---- 连接与凭据 ---- */

export type ConnectionType =
  | 'MYSQL'
  | 'POSTGRESQL'
  | 'DORIS'
  | 'HIVE'
  | 'SQL_SERVER'
  | 'LOCAL_FILE'
  | 'CLICKHOUSE'
  | 'S3'
  | 'REST_API'
export type ConnectionStatus = 'UNTESTED' | 'TESTING' | 'READY' | 'FAILED'

/**
 * 连接视图（对齐后端 `ConnectionResponse`，`connections/schemas.py`）。
 *
 * **绝不含 password / ciphertext**（AC-02）；`secret_ref` 只是托管引用 id。
 * 历史坑：前端曾声明 `secret_provider` / `secret_age_days` / `expiry_warning` /
 * `last_tested_at` / `endpoint_role` 五个后端**不存在**的字段 → 表格三列恒「—」，
 * 且 Studio 靠 `endpoint_role` 找 source/sink 连接**恒失败**。已随
 * `fix/S10-console-contract-a` 删除；端点角色改从 `ProfileView.endpoint_role` 取。
 */
export interface ConnectionView {
  id: string
  name: string
  connector_type: ConnectionType
  status: ConnectionStatus
  host: string | null
  port: number | null
  database_name: string | null
  username: string
  /** 凭据托管引用（无值表示尚未托管） */
  secret_ref: string | null
  credential_max_age_days: number
  credential_warning_days: number
  created_at: string
}

export interface ConnectionCreateRequest {
  name: string
  connector_type: ConnectionType
  host: string
  port: number
  database: string
  username: string
  password: string
}

/**
 * 连通性测试受理响应（对齐后端 `ConnectionTestResponse`：后台异步执行，只回 `status` + `started_at`）。
 *
 * 曾多声明 `message` / `latency_ms` —— 后端不同步这两项（后台执行，无耗时回传）。
 */
export interface ConnectionTestResponse {
  status: ConnectionStatus
  started_at: string
}

/**
 * 凭据轮换**列表**项（对齐后端 `RotationListView`）。
 *
 * 轮换**操作**出参是另一套模型 `RotationResponseView`（`new_secret_ref` /
 * `requested_by` / `approved_by` / `failure_reason` / `created_at`），不可混用。
 */
export interface RotationView {
  id: string
  connection_id: string
  status: string
  reason: string | null
  staged_at: string | null
  validated_at: string | null
  activated_at: string | null
  rolled_back_at: string | null
}

/** 轮换**操作**出参（对齐后端 `RotationResponse`）。 */
export interface RotationResponseView {
  id: string
  connection_id: string
  status: string
  new_secret_ref: string | null
  requested_by: string
  approved_by: string | null
  failure_reason: string | null
  created_at: string
}

/* ---- 探查与文件 ---- */

export interface ProfileView {
  id: string
  connection_id: string
  schema_name: string
  table_name: string
  endpoint_role: 'SOURCE' | 'SINK'
  column_count: number
  primary_key: string[] | null
  report_sha256: string
  classifications: Record<string, number>
  sample_status: string
  created_at: string
}

/**
 * 文件资产（对齐后端 `FileAssetListView`）。
 *
 * 注意上传接口（`POST /file-assets`）回的是 `FileAssetDetailView`（无
 * `filename` / `upload_status`），不要混用。
 */
export interface FileAssetView {
  id: string
  filename: string
  size_bytes: number
  row_count: number | null
  format: string
  upload_status: string
  created_at: string
}

/** 上传出参（对齐后端 `FileAssetResponse`：无文件名 / 状态字段）。 */
export interface FileAssetDetailView {
  id: string
  format: string
  size_bytes: number
  row_count: number | null
  created_at: string
}

/**
 * 探查**创建**出参（对齐后端 `ProfileResponse`；含列定义 / 脱敏样本 / 行数估计）。
 *
 * 与列表项 `ProfileView`（= 后端 `ProfileSummaryView`）是两套模型：
 * 列表只回摘要（`column_count` / `report_sha256` / `classifications`），
 * 创建回全量（`columns` / `masked_samples` / `approximate_row_count`）。
 */
export interface ProfileResponseView {
  id: string
  connection_id: string
  endpoint_role: 'SOURCE' | 'SINK'
  schema_name: string
  table_name: string
  columns: ProfileColumnView[]
  masked_samples: Record<string, unknown> | null
  approximate_row_count: number | null
  classification_report_sha256: string | null
  expires_at: string | null
  created_at: string
}

/** 列画像（对齐后端 `ProfileColumn`）。 */
export interface ProfileColumnView {
  name: string
  type: string
  nullable: boolean
  is_primary_key: boolean
  null_rate: number | null
  distinct_estimate: number | null
  classification: string | null
  effective_classification: string | null
}

/* ---- 生成与版本 ---- */

/**
 * 运行列表项（对齐后端 `AgentRunListView`，`generation/schemas.py`）。
 *
 * 只用于 `GET /projects/{id}/agent-runs`。**创建/续跑**（POST）回的是
 * `AgentRunResponseView`（字段集不同，见下）——两者不可混用。
 */
export interface AgentRunView {
  id: string
  thread_id: string
  pipeline_id: string
  /** AMEND-010：本次运行绑定的版本 id（列表出参直给，Studio 免去「按 pipeline 猜版本」） */
  pipeline_version_id: string
  status: string
  repair_count: number
  /** 挂起的澄清问题（列表口径；创建/续跑口径叫 `pending_question`） */
  clarify_question: string | null
  created_at: string | null
}

/**
 * 创建 / 续跑的运行响应（对齐后端 `AgentRunResponse`，`generation/schemas.py`）。
 *
 * 与 `AgentRunView` 的差异（历史坑，`fix/S10-console-contract-a` 修正）：
 * 无 `pipeline_id` / `repair_count` / `created_at`，问题字段叫 **`pending_question`**
 * —— 前端曾按 `clarify_question` 读，导致「第二轮澄清问题不显示」。
 */
export interface AgentRunResponseView {
  id: string
  thread_id: string
  status: string
  pipeline_version_id: string | null
  /** 挂起的澄清问题（创建/续跑口径） */
  pending_question: string | null
  generation_backend: string | null
  generation_model: string | null
  generation_candidate_count: number | null
  /** 修回次数（后端字段名 `generation_repair_count`，非 `repair_count`） */
  generation_repair_count: number | null
  generation_duration_ms: number | null
}

/**
 * 版本摘要（对齐后端 `PipelineVersionResponse`，`versions/schemas.py:24-34`）。
 *
 * **口径（SPEC 6.2 / FR-67）**：本端点只回**摘要与状态**；候选 plan / HOCON 正文留在
 * `etl_checkpoint`，**不在本出参内**。故前端不得从本对象取 `hocon` / `etl_plan`
 * （历史上曾声明这两个字段，恒 undefined → 页面回落演示正文，已随
 * `fix/S10-console-contract-a` 删除）。
 */
export interface PipelineVersionView {
  id: string
  pipeline_id: string
  /** 版本号（后端字段名 `version_number`） */
  version_number: number
  status: string
  artifact_sha256: string | null
  parent_version_id: string | null
  /** 乐观锁版本（并发冻结/派生保护） */
  lock_version: number
  /** 制品摘要（只有类型 + SHA256，**无正文**） */
  artifacts: VersionArtifactView[]
}

/** 制品摘要行（`versions/schemas.py::VersionArtifact`）。 */
export interface VersionArtifactView {
  artifact_type: string
  sha256: string
}

/** 冻结出参（对齐后端 `FreezeResponse`：版本级摘要 + 制品明细，**无** `version_number` 等）。 */
export interface FreezeResponseView {
  id: string
  artifact_sha256: string
  status: string
  artifacts: VersionArtifactView[]
}

/** 派生新版本出参（对齐后端 `DeriveResponse`；新版本为 DRAFT，无 `artifact_sha256`）。 */
export interface DeriveResponseView {
  id: string
  pipeline_id: string
  version_number: number
  status: string
  parent_version_id: string | null
}

export interface GateResultView {
  rule_id: string
  name: string
  passed: boolean
  detail: string | null
}

/**
 * 版本 Diff（对齐后端 `VersionDiffResponse`，`src/etl_agent/versions/schemas.py:97-106`）。
 *
 * 口径：业务库只有制品**摘要**，正文在 `etl_checkpoint`。故：
 * - `artifact_digests` **恒可用**（摘要级差异）；
 * - `schema_diff` / `quality_contract_diff` / `hocon_diff` 仅在 `content_available=true` 时非空。
 */
export interface VersionDiffView {
  version_id: string
  against_version_id: string
  /** `artifact_type → { before, after, changed }`（SHA256 摘要级，恒可用） */
  artifact_digests: Record<string, ArtifactDigestDiff>
  /** 两版本候选正文是否都能从 Checkpoint 取到（false 时三个正文 diff 均为 null） */
  content_available: boolean
  schema_diff: SchemaDiffPayload | null
  quality_contract_diff: QualityDiffPayload | null
  hocon_diff: HoconDiffPayload | null
}

/** 制品摘要级差异（`versions/diff.py::diff_artifact_digests`）。 */
export interface ArtifactDigestDiff {
  before: string
  after: string
  changed: boolean
}

/** Schema 差异（`versions/diff.py::diff_columns`）。 */
export interface SchemaDiffPayload {
  added: string[]
  removed: string[]
  type_changed: { name: string; before: string; after: string }[]
}

/** 质量契约差异（`versions/diff.py::diff_quality_contract`）。 */
export interface QualityDiffPayload {
  added: string[]
  removed: string[]
  changed: { rule: string; before: unknown; after: unknown }[]
}

/** HOCON 行级差异（`versions/diff.py::diff_hocon`；只回计数，不回正文）。 */
export interface HoconDiffPayload {
  changed: boolean
  added_lines: number
  removed_lines: number
  before_line_count: number
  after_line_count: number
}

export interface SchemaChangeView {
  change_type: 'ADDED' | 'REMOVED' | 'TYPE_CHANGED'
  object: string
  before: string | null
  after: string | null
}

export interface HoconChangeView {
  object: string
  before: string | null
  after: string | null
}

/* ---- Harness（准备单 / 审批 / 提交） ---- */

/**
 * 准备单**列表**项（对齐后端 `PreparationListView`；前端审批台消费此口径）。
 *
 * 同一资源另有创建出参 `PreparationCreateResponseView` 与决策出参
 * `DecisionResponseView` —— 三套模型字段集不同，不可混用。
 */
export interface PreparationView {
  id: string
  pipeline_version_id: string
  decision: Decision
  risk_level: RiskLevel
  status: string
  required_roles: string[]
  required_approvals: number
  approvals: ApprovalSlotView[]
  impact_report: Record<string, unknown> | null
  rollback_plan: string | null
  expires_at: string | null
  created_at: string | null
}

export interface ApprovalSlotView {
  role: string
  decided_by: string | null
  decision: string | null
  reason: string | null
  evidence_sha256: string | null
  decided_at: string | null
}

export interface DecisionRequest {
  role: string
  decision: 'APPROVE' | 'REJECT'
  reason: string
}

/** 准备单**创建**出参（对齐后端 `PreparationResponse`；不含审批槽位 / PDP 判决）。 */
export interface PreparationCreateResponseView {
  id: string
  status: string
  risk_level: RiskLevel
  artifact_sha256: string | null
  input_fingerprint: string
  resource_fingerprint: string
  required_roles: string[]
  required_approvals: number
  environment: string
  tool_name: string | null
  tool_version: string | null
  expires_at: string | null
  outcome: string | null
}

/** 审批**决策**出参（对齐后端 `DecisionResponse`）。 */
export interface DecisionResponseView {
  preparation_id: string
  role: string
  decision: string
  decided_by: string | null
  status: string
  approvals_received: number
  approvals_required: number
}

export interface CommitResponse {
  /** 单次 Capability 令牌 id（**不含令牌本体**，FR-33） */
  token_id: string
  preparation_id: string
  claims: Record<string, unknown>
  expires_at: string
  /** 本次 Commit 生成的执行运行 id（后端字段名 `run_id`，非 `execution_run_id`） */
  run_id: string
  outbox_id: string | null
}

/* ---- 执行与监督 ---- */

export type ExecutionStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'QUARANTINED'

/** 执行事件流条目（对齐后端 `ExecutionEventView`：level / stage / event_type / message / occurred_at）。 */
export interface ExecutionEventView {
  level: string
  stage: string | null
  event_type: string
  message: string
  occurred_at: string
}

export interface ExecutionRunView {
  id: string
  pipeline_version_id: string
  pipeline_name: string | null
  status: ExecutionStatus
  trigger_mode: 'MANUAL' | 'SCHEDULED'
  retry_count: number
  source_rows: number | null
  target_rows: number | null
  bytes_processed: number | null
  started_at: string | null
  finished_at: string | null
  supervision_action: string | null
  supervision_risk: RiskLevel | null
  snapshot_sha256: string | null
}

/**
 * 执行运行**详情**（对齐后端 `ExecutionDetailResponse` = 后端 `ExecutionRunView` + events + 监督快照）。
 *
 * **不能 `extends ExecutionRunView`**：前端 `ExecutionRunView` 对齐的是**列表**模型
 * `ExecutionRunListView`（展示口径 `trigger_mode` / `target_rows` / `bytes_processed`），
 * 而详情出参用的是**列名口径**（`trigger` / `sink_rows` / `source_bytes` / `sink_bytes`）。
 * 两者是同一事实的两种命名 —— 混用会让详情页一半字段恒 `undefined`
 * （`fix/S10-console-contract-a` 修正；由 `npm run check:contract` 发现）。
 */
export interface ExecutionDetailView {
  id: string
  pipeline_version_id: string
  harness_preparation_id: string | null
  status: ExecutionStatus
  trigger: 'MANUAL' | 'SCHEDULED'
  seatunnel_job_id: string | null
  engine_status: string | null
  scheduled_for: string | null
  source_rows: number | null
  sink_rows: number | null
  source_bytes: number | null
  sink_bytes: number | null
  table_metrics: Record<string, unknown> | null
  retry_of_run_id: string | null
  attempt_number: number
  failure_category: string | null
  failure_stage: string | null
  retryable: boolean | null
  diagnosis: string | null
  submitted_at: string | null
  started_at: string | null
  finished_at: string | null
  last_observed_at: string | null
  events: ExecutionEventView[]
  supervision_snapshot: Record<string, unknown> | null
}

/**
 * 质量报告（对齐后端 `QualityReportView`，`execution/schemas.py:118-131`）。
 *
 * 历史坑（`fix/S10-console-contract-a` 修正）：
 * - 曾声明 `error_code_distribution`，后端**无此字段** → 「错误码分布」区块恒不渲染；
 *   错误码实际在 `diagnostics` 里（`kind='error_codes'` 的 `counts`）。
 * - `diagnostics` 曾被写成 `string[]`，实际是**对象数组** → 页面把对象原样插值成 JSON 文本。
 */
export interface QualityReportView {
  run_id: string
  status: 'PASSED' | 'WARNING' | 'FAILED' | 'INCOMPLETE'
  total_rows: number | null
  passed_rows: number | null
  rejected_rows: number | null
  reject_rate: number | null
  valid_sink: string | null
  error_sink: string | null
  rules: QualityRuleRefView[]
  diagnostics: QualityDiagnosticView[]
  generated_at: string | null
}

/** 契约规则引用（只引名称 / 表达式 / 错误码，不含业务正文）。 */
export interface QualityRuleRefView {
  name: string
  expression: string
  code?: string
}

/** 诊断条目（`{kind:'error_codes', counts}` / `{kind:'quarantined', detail}` / `{kind:'insufficient_rows', detail}` 等）。 */
export interface QualityDiagnosticView {
  kind: string
  counts?: Record<string, number>
  detail?: string
}

/* ---- 回滚 ---- */

/**
 * 回滚申请**列表**项（对齐后端 `RollbackListView`）。
 *
 * 与 `RollbackView`（详情）是**两套出参**：列表用 `execution_run_id`（= 后端 `run_id` 的
 * 列表别名）且 `risk_level` 恒 `null`（PDP 风险判决落在 Harness 副作用准备单上，
 * 回滚申请表无该列，后端不跨域拼接）。
 */
export interface RollbackListView {
  id: string
  execution_run_id: string
  status: string
  /** 后端如实回 `null`（回滚申请表无风险列） */
  risk_level: RiskLevel | null
  artifact_manifest: Record<string, unknown> | null
  created_at: string
}

/** 回滚申请**详情**（对齐后端 `RollbackResponse`；`run_id` 非 `execution_run_id`）。 */
export interface RollbackView {
  id: string
  /** 关联执行运行（详情口径字段名 `run_id`） */
  run_id: string
  status: string
  reason: string | null
  requested_by: string
  decided_by: string | null
  decision: string | null
  retention_days: number
  capacity_warn_threshold: number
  artifact_status: string | null
  artifact_manifest: Record<string, unknown> | null
  executed_at: string | null
  created_at: string
  updated_at: string
}

export interface RollbackCleanupView {
  id: string
  rollback_request_id: string | null
  status: string
  reason: string | null
  requested_by: string
  executed_at: string | null
  created_at: string
}

export interface CleanupScanResponse {
  evaluated: number
  created: number
  skipped: number
  retention_summary: string
  capacity_warning: boolean
  details: Record<string, string>[]
}

/* ---- 证据账本 ---- */

export interface EvidenceStreamView {
  id: string
  project_id: string
  head_hash: string
  created_at: string
}

export interface EvidenceEventView {
  id: string
  stream_id: string
  sequence: number
  payload_sha256: string
  previous_hash: string
  event_hash: string
  signature_algorithm: string | null
  signature_key_id: string | null
  occurred_at: string
  actor_id: string
  event_type: string
  risk_level: RiskLevel | null
  policy_decision_id: string | null
  trace_id: string | null
}

export interface VerifyResponse {
  valid: boolean
  event_count: number
  head_hash: string
  errors: string[]
}

/* ---- Benchmark ---- */

export interface GateOutcomeView {
  key: string
  actual: number
  operator: string
  threshold: number
  passed: boolean
}

export interface LayerSummaryView {
  layer: string
  total: number
  passed: number
  blocked: number
}

export interface BenchmarkRunView {
  run_id: string
  suite_id: string
  manifest_version: string
  manifest_sha256: string
  status: 'PASS' | 'FAIL'
  metrics: Record<string, number>
  gates: GateOutcomeView[]
  failures: string[]
  layers: LayerSummaryView[]
  report_sha256: string
  trace_id?: string | null
}

/**
 * 评测运行**详情**（对齐后端 `BenchmarkRunDetailView`，`benchmark/schemas.py`）。
 *
 * 与 `BenchmarkRunView`（= `BenchmarkRunResponse`，列表/创建口径）不同：
 * 详情端点**不含** `gates` / `failures` / `layers` / `trace_id`，只回 `metrics` 与创建时间。
 * 历史坑：`getBenchmarkRun` 曾按 `BenchmarkRunView` 标注 → 详情端点字段对不上。
 */
export interface BenchmarkRunDetailView {
  run_id: string
  suite_id: string
  manifest_version: string
  manifest_sha256: string
  status: string
  metrics: Record<string, number>
  report_sha256: string | null
  created_at: string
}

/* ---- 安全提案与受控发布 ---- */

export interface ProposalView {
  id: string
  project_id: string
  benchmark_run_id: string | null
  /** 提案类型（后端字段名 `type`，曾误写 `proposal_type`） */
  type: 'POLICY' | 'PROMPT' | 'BENCHMARK'
  status: string
  title: string
  summary: string | null
  created_by: string
  decided_by: string | null
  decision: string | null
  decision_reason: string | null
  decided_at: string | null
  created_at: string
  updated_at: string
}

export interface ReleaseView {
  id: string
  proposal_id: string
  /** 发布阶段（后端字段名 `stage`，曾误写 `release_stage`） */
  stage: string
  canary_percent: number | null
  manifest_sha256: string
  /** 签名密钥 id（后端字段名 `signature_key_id`，曾误写 `release_signature`） */
  signature_key_id: string | null
  created_at: string
  updated_at: string
}

export interface ObservationView {
  sample_size: number
  anomaly_count: number
  anomaly_rate: number
  aggregated: boolean
}

export interface ProposalDecisionRequest {
  decision: 'APPROVE' | 'REJECT'
  reason: string
}
