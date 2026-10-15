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

export interface BootstrapResponse {
  project_id: string
  project_name: string
  admin_user_id: string
  created: boolean
}

export interface MemberView {
  user_id: string
  subject: string
  role: string
  created_at: string
}

export interface RoleGrantView {
  id: string
  user_id: string
  role: string
  granted_at: string
}

/* ---- 连接与凭据 ---- */

export type ConnectionType = 'MYSQL' | 'POSTGRESQL' | 'DORIS' | 'HIVE' | 'SQL_SERVER' | 'LOCAL_FILE'
export type ConnectionStatus = 'UNTESTED' | 'TESTING' | 'READY' | 'FAILED'

export interface ConnectionView {
  id: string
  name: string
  connector_type: ConnectionType
  status: ConnectionStatus
  secret_provider: string | null
  secret_age_days: number | null
  expiry_warning: string | null
  last_tested_at: string | null
  endpoint_role: 'SOURCE' | 'SINK' | null
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

export interface ConnectionTestResponse {
  status: ConnectionStatus
  message: string
  latency_ms: number | null
}

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

export interface FileAssetView {
  id: string
  filename: string
  size_bytes: number
  row_count: number | null
  format: string
  upload_status: string
  created_at: string
}

/* ---- 生成与版本 ---- */

export interface AgentRunView {
  id: string
  thread_id: string
  pipeline_id: string
  status: string
  stage: string | null
  repair_count: number
  telemetry: Record<string, unknown>
  clarify_question: string | null
  created_at: string
}

export interface PipelineVersionView {
  id: string
  pipeline_id: string
  version_no: number
  status: string
  artifact_sha256: string
  parent_version_id: string | null
  hocon: string | null
  etl_plan: Record<string, unknown> | null
  frozen_at: string | null
  created_at: string
}

export interface GateResultView {
  rule_id: string
  name: string
  passed: boolean
  detail: string | null
}

export interface VersionDiffView {
  base_version_id: string
  target_version_id: string
  schema_changes: SchemaChangeView[]
  hocon_changes: HoconChangeView[]
  quality_changes: HoconChangeView[]
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
  expires_at: string
  created_at: string
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

export interface CommitResponse {
  execution_run_id: string
  capability_token_id: string
  capability_expires_at: string
}

/* ---- 执行与监督 ---- */

export type ExecutionStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'QUARANTINED'

export interface ExecutionEventView {
  id: string
  event_type: string
  message: string | null
  payload: Record<string, unknown> | null
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

export interface ExecutionDetailView extends ExecutionRunView {
  events: ExecutionEventView[]
  failure_category: string | null
  failure_stage: string | null
  retryable: boolean | null
  recommendation: string | null
}

export interface QualityReportView {
  run_id: string
  status: 'PASSED' | 'WARNING' | 'FAILED' | 'INCOMPLETE'
  total_rows: number
  passed_rows: number
  rejected_rows: number
  reject_rate: number
  error_code_distribution: Record<string, number>
  diagnostics: string[]
}

/* ---- 回滚 ---- */

export interface RollbackView {
  id: string
  execution_run_id: string
  status: string
  risk_level: RiskLevel
  artifact_manifest: Record<string, unknown> | null
  created_at: string
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

/* ---- 安全提案与受控发布 ---- */

export interface ProposalView {
  id: string
  proposal_type: 'POLICY' | 'PROMPT' | 'BENCHMARK'
  status: string
  title: string
  source_benchmark_run_id: string | null
  decided_by: string | null
  decision_reason: string | null
  release_id: string | null
  created_at: string
}

export interface ReleaseView {
  id: string
  proposal_id: string
  release_stage: string
  canary_percent: number | null
  manifest_sha256: string
  release_signature: string | null
  rolled_back_at: string | null
  created_at: string
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
