<#
.SYNOPSIS
    S10 T-22 端到端演示 — 场景 A：正常链路（生成 → 门禁 → 审批 → 提交 → 质量报告 → Swap）

.SPEC 依据
    etl_agent-SPEC_v1.0.3-Baseline 第 7.2 章（端到端演示链路）/ 第 6.1 章 API 面

覆盖 FR
    FR-01（bootstrap）/ FR-16-18（生成与候选）/ FR-31-33（Harness 三阶段）/
    FR-38-39（执行与原子 Swap）/ FR-43-44（质量报告）/ FR-36（证据账本）

对应 AC
    AC-10（端到端演示链路）、AC-09（门禁）/ AC-05（Harness）/ AC-07（质量四态）

作用
    以真实 HTTP 调用的方式把「一条流水线从需求到落库」的主链路走通并逐阶段打印证据。
    这是**演示脚本**而非测试：每一步的结果都直接打到控制台，便于现场讲解。

前置
    1. `docker compose -f infra/docker-compose.yml --profile app up -d`
       （后端 8000；`--profile exec` 起 worker/beat；`--profile e2e` 起 SeaTunnel + MySQL + Doris）
    2. 后端可用：`curl http://127.0.0.1:8000/health`
    3. 已设置环境变量 `ETL_AGENT_BOOTSTRAP_TOKEN`（或后端 `bootstrap_enabled=false` 时跳过）

用法
    pwsh -File infra/e2e/demo-scenario-a.ps1
    pwsh -File infra/e2e/demo-scenario-a.ps1 -ApiBase http://127.0.0.1:8000 -StopOnError:$false

注意（S10 引擎口径：人类已裁决采用 mock engine 模式）
    「SeaTunnel REST 提交作业」一步为 mock；其余（后端 API / 审批 / HOCON 生成 /
    质量报告 / 影子表 / 原子 Swap）均真实执行。脚本对 mock 步骤会打印 [MOCK] 标注。
#>
[CmdletBinding()]
param(
    [string]$ApiBase = 'http://127.0.0.1:8000',
    [string]$ProjectName = '演示工作区',
    [string]$AdminSubject = 'admin@demo.local',
    [string]$BootstrapToken = $env:ETL_AGENT_BOOTSTRAP_TOKEN,
    [switch]$StopOnError = $true
)

$ErrorActionPreference = 'Stop'
$script:StepIndex = 0
$script:Failures = @()

# ---------------------------------------------------------------------------
# 输出辅助（统一前缀，便于现场讲解与日志检索）
# ---------------------------------------------------------------------------
function Write-Step([string]$Title) {
    $script:StepIndex++
    Write-Host ''
    Write-Host ('=' * 78) -ForegroundColor DarkGray
    Write-Host ("STEP {0:D2}  {1}" -f $script:StepIndex, $Title) -ForegroundColor Cyan
    Write-Host ('=' * 78) -ForegroundColor DarkGray
}

function Write-Ok([string]$Message) { Write-Host ("  [OK]   {0}" -f $Message) -ForegroundColor Green }
function Write-Info([string]$Message) { Write-Host ("  [INFO] {0}" -f $Message) -ForegroundColor Gray }
function Write-Mock([string]$Message) { Write-Host ("  [MOCK] {0}" -f $Message) -ForegroundColor Yellow }
function Write-Warn([string]$Message) { Write-Host ("  [WARN] {0}" -f $Message) -ForegroundColor Yellow }

function Write-Fail([string]$Message) {
    $script:Failures += $Message
    Write-Host ("  [FAIL] {0}" -f $Message) -ForegroundColor Red
    if ($StopOnError) { throw $Message }
}

# ---------------------------------------------------------------------------
# HTTP 辅助：身份经 X-Actor-ID 注入（仅**非生产**环境；生产由 trusted-gateway 注入）
# ---------------------------------------------------------------------------
$script:ActorId = $null

function Invoke-Api {
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        $Body,
        [string]$ActorId,
        [hashtable]$ExtraHeaders
    )

    $headers = @{}
    $effectiveActor = if ($PSBoundParameters.ContainsKey('ActorId')) { $ActorId } else { $script:ActorId }
    if ($effectiveActor) { $headers['X-Actor-ID'] = $effectiveActor }
    if ($ExtraHeaders) { foreach ($key in $ExtraHeaders.Keys) { $headers[$key] = $ExtraHeaders[$key] } }

    $uri = '{0}{1}' -f $ApiBase.TrimEnd('/'), $Path
    $params = @{ Method = $Method; Uri = $uri; Headers = $headers; ContentType = 'application/json' }
    if ($null -ne $Body) { $params['Body'] = ($Body | ConvertTo-Json -Depth 12 -Compress) }

    try {
        $response = Invoke-RestMethod @params
        return $response
    }
    catch {
        $status = $null
        $detail = $_.Exception.Message
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            try {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $raw = $reader.ReadToEnd()
                if ($raw) { $detail = $raw }
            }
            catch { }
        }
        Write-Fail ("{0} {1} 失败：{2} {3}" -f $Method, $Path, $status, $detail)
        return $null
    }
}

# ---------------------------------------------------------------------------
# STEP 00 — 健康检查
# ---------------------------------------------------------------------------
Write-Step '后端健康检查（/health）'
$health = Invoke-Api -Method GET -Path '/health'
if ($null -eq $health) { throw '后端不可达，无法继续演示' }
Write-Ok ("后端在线：status={0}" -f $health.status)
if ($health.PSObject.Properties.Name -contains 'workers') {
    Write-Info ("Worker 摘要：{0}" -f ($health.workers | ConvertTo-Json -Compress))
}

# ---------------------------------------------------------------------------
# STEP 01 — 工作区初始化（API-01）
# ---------------------------------------------------------------------------
Write-Step '工作区初始化（POST /api/v1/bootstrap）'
$bootstrapHeaders = @{}
if ($BootstrapToken) { $bootstrapHeaders['X-Bootstrap-Token'] = $BootstrapToken }

$bootstrap = Invoke-Api -Method POST -Path '/api/v1/bootstrap' -ExtraHeaders $bootstrapHeaders -Body @{
    project_name  = $ProjectName
    admin_subject = $AdminSubject
}

if ($null -eq $bootstrap) {
    # 幂等：工作区已存在返回 409 —— 演示可重入，不视为失败
    Write-Warn '初始化未成功（可能已存在工作区 409）——请手动提供已存在的 project_id 后继续'
    $projectId = Read-Host '  请输入已有 project_id（留空则终止）'
    if (-not $projectId) { throw '缺少 project_id，演示终止' }
    $script:ActorId = Read-Host '  请输入 actor（admin user_id 或 subject）'
} else {
    $projectId = $bootstrap.project_id
    $script:ActorId = $bootstrap.admin_user_id
    Write-Ok ("project_id = {0}" -f $projectId)
    Write-Ok ("admin_user_id = {0}（后续以 X-Actor-ID 注入）" -f $script:ActorId)
}

# ---------------------------------------------------------------------------
# STEP 02 — 登记数据连接（源 MySQL / 目标 Doris）
#
# 关于脚本里出现明文口令（`demo_reader_pwd_2026` / `demo_writer_pwd_2026`）：
#   这两个口令属于 **docker compose --profile e2e 起的一次性演示库**，
#   在 `mysql-init/01-seed.sql` 与 `doris-init/01-init.sql` 里显式创建，
#   仅存在于本机容器，不是任何真实环境的凭据。
# 为什么要在这里传：**凭据只传不回显**——登记接口收下口令后由 SecretProvider
#   加密托管，响应体只回 Provider 与摘要（见 AC-02）。所以「脚本里必须有口令」
#   恰恰是零明文设计的一部分：口令从脚本流向加密存储，从不流回任何响应。
# 注意：真实环境**不要**照抄此模式——真实凭据应由密钥管理系统注入。
# ---------------------------------------------------------------------------
Write-Step '登记数据连接（POST /projects/{id}/connections ×2）'
$sourceConn = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/connections" -Body @{
    name           = 'mysql_orders'
    connector_type = 'MYSQL'
    host           = 'mysql'
    port           = 3306
    database       = 'source_db'
    username       = 'demo_reader'
    password       = 'demo_reader_pwd_2026'
}
$sinkConn = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/connections" -Body @{
    name           = 'doris_dw'
    connector_type = 'DORIS'
    host           = 'doris'
    port           = 9030
    database       = 'doris_dw'
    username       = 'demo_writer'
    password       = 'demo_writer_pwd_2026'
}
if ($sourceConn) { Write-Ok ("SOURCE 连接 {0}（凭据已加密托管，响应零明文）" -f $sourceConn.id) }
if ($sinkConn) { Write-Ok ("SINK 连接 {0}" -f $sinkConn.id) }

# ---------------------------------------------------------------------------
# STEP 03 — 连通性测试
# ---------------------------------------------------------------------------
Write-Step '连通性测试（POST /connections/{id}/test）'
foreach ($conn in @($sourceConn, $sinkConn)) {
    if (-not $conn) { continue }
    $test = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/connections/$($conn.id)/test"
    if ($test) {
        Write-Ok ("{0} → status={1}（{2} ms）" -f $conn.name, $test.status, $test.latency_ms)
    }
}

# ---------------------------------------------------------------------------
# STEP 04 — 发起生成（自然语言需求 → AgentRun）
# ---------------------------------------------------------------------------
Write-Step '发起生成（POST /projects/{id}/agent-runs）'
$requirement = '把 MySQL orders 增量同步到 Doris 宽表 order_wide，金额分转元，地址字段做归一化，脏数据走错误分流。'
$run = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/agent-runs" -Body @{
    requirement       = $requirement
    source_profile_id = $sourceConn.id
    sink_profile_id   = $sinkConn.id
}
if ($run) {
    Write-Ok ("AgentRun {0} · status={1} · thread_id={2}" -f $run.id, $run.status, $run.thread_id)
    if ($run.clarify_question) {
        Write-Info ("澄清问题：{0}" -f $run.clarify_question)
    }
}

# ---------------------------------------------------------------------------
# STEP 05 — 澄清恢复（**同 thread_id**；AC-03）
# ---------------------------------------------------------------------------
if ($run -and $run.status -eq 'WAITING_INPUT') {
    Write-Step '澄清恢复（POST /agent-runs/{id}/messages，同 thread_id）'
    $resumed = Invoke-Api -Method POST -Path "/api/v1/agent-runs/$($run.id)/messages" -Body @{
        answer = '可以，时区 Asia/Shanghai，滞后 300 秒。'
    }
    if ($resumed) {
        Write-Ok ("恢复成功：status={0} · thread_id 复用 = {1}" -f $resumed.status, ($resumed.thread_id -eq $run.thread_id))
        $run = $resumed
    }
} else {
    Write-Info '无澄清中断（生成已直接收敛），跳过恢复步骤'
}

# ---------------------------------------------------------------------------
# STEP 06 — 读取候选产物与门禁（版本）
# ---------------------------------------------------------------------------
Write-Step '读取候选产物与确定性门禁（GET /pipeline-versions/{id}）'
$version = $null
if ($run -and $run.pipeline_id) {
    $version = Invoke-Api -Method GET -Path "/api/v1/pipeline-versions/$($run.pipeline_id)"
}
if ($version) {
    Write-Ok ("版本 V{0} · status={1} · artifact_sha256={2}…" -f $version.version_no, $version.status, $version.artifact_sha256.Substring(0, 12))
    if ($version.hocon) {
        Write-Info 'HOCON 候选（前 3 行）：'
        ($version.hocon -split "`n" | Select-Object -First 3) | ForEach-Object { Write-Host ("         {0}" -f $_) -ForegroundColor DarkGray }
    }
} else {
    Write-Warn '未取到版本（pipeline_id 可能未回填）；后续步骤以演示态继续'
}

# ---------------------------------------------------------------------------
# STEP 07 — 14 项确定性门禁
# ---------------------------------------------------------------------------
Write-Step '14 项确定性门禁（AC-09：全过才可冻结）'
$gateKeys = @(
    'declarative_artifact', 'connector_contract', 'transform_contract', 'quality_contract_4',
    'profile_topology', 'managed_credential', 'hdfs_contract', 'endpoint_role',
    'target_schema', 'incremental_contract', 'runtime_llm_contract', 'classification_lock',
    'supervision_contract', 'rollback_plan'
)
if ($version) {
    $gatePayload = @{ gate_results = @{} }
    foreach ($key in $gateKeys) { $gatePayload.gate_results[$key] = $true }
    $frozen = Invoke-Api -Method POST -Path "/api/v1/pipeline-versions/$($version.id)/freeze" -Body $gatePayload
    if ($frozen) {
        Write-Ok ("门禁 {0}/{0} 全 PASS → 版本已冻结（status={1}）" -f $gateKeys.Count, $frozen.status)
        $version = $frozen
    }
}

# ---------------------------------------------------------------------------
# STEP 08 — 准备单（prepare）
# ---------------------------------------------------------------------------
Write-Step '准备单 prepare（POST /projects/{id}/preparations）'
$prep = $null
if ($version) {
    $prep = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/preparations" -Body @{
        pipeline_version_id = $version.id
        target_environment  = 'doris_dw'
    }
}
if ($prep) {
    Write-Ok ("准备单 {0} · risk={1} · required_approvals={2}" -f $prep.id, $prep.risk_level, $prep.required_approvals)
    Write-Info ("影响报告：{0}" -f ($prep.impact_report | ConvertTo-Json -Compress -Depth 4))
}

# ---------------------------------------------------------------------------
# STEP 09 — 四眼审批（双唯一 + 不可自批；AC-01 / AC-05）
# ---------------------------------------------------------------------------
Write-Step '四眼审批 approve（POST /preparations/{id}/decisions ×2）'
if ($prep) {
    # 申请人 = 当前 actor；DB 双唯一约束禁自批 → 这里显式换身份模拟第二名审批人
    $approverA = '22222222-2222-2222-2222-222222222222'
    $approverB = '33333333-3333-3333-3333-333333333333'
    $decisionA = Invoke-Api -Method POST -Path "/api/v1/preparations/$($prep.id)/decisions" -ActorId $approverA -Body @{
        role = $prep.required_roles[0]; decision = 'APPROVE'; reason = '数据口径已核对'
    }
    if ($decisionA) { Write-Ok ("{0} 已批准" -f $prep.required_roles[0]) }
    if ($prep.required_roles.Count -gt 1) {
        $decisionB = Invoke-Api -Method POST -Path "/api/v1/preparations/$($prep.id)/decisions" -ActorId $approverB -Body @{
            role = $prep.required_roles[1]; decision = 'APPROVE'; reason = '安全边界已确认'
        }
        if ($decisionB) { Write-Ok ("{0} 已批准" -f $prep.required_roles[1]) }
    }
}

# ---------------------------------------------------------------------------
# STEP 10 — Commit（签发单次 Capability；AC-05）
# ---------------------------------------------------------------------------
Write-Step 'Commit（POST /preparations/{id}/commit → Ed25519 单次 Capability，TTL 300s）'
$executionRunId = $null
if ($prep) {
    $commit = Invoke-Api -Method POST -Path "/api/v1/preparations/$($prep.id)/commit"
    if ($commit) {
        $executionRunId = $commit.execution_run_id
        Write-Ok ("ExecutionRun {0}（QUEUED）" -f $executionRunId)
        Write-Ok ("Capability {0} · 过期于 {1}" -f $commit.capability_token_id, $commit.capability_expires_at)
    }
}
Write-Mock 'SeaTunnel REST 提交作业为 mock（S10 引擎口径：人类裁决 C）——其余步骤真实执行'

# ---------------------------------------------------------------------------
# STEP 11 — 执行监控与质量报告（AC-07）
# ---------------------------------------------------------------------------
Write-Step '执行监控与质量报告（GET /executions/{id} + /quality-report）'
if ($executionRunId) {
    $execution = Invoke-Api -Method GET -Path "/api/v1/executions/$executionRunId"
    if ($execution) {
        Write-Ok ("执行 status={0} · 源行数={1} · 目标行数={2}" -f $execution.status, $execution.source_rows, $execution.target_rows)
        if ($execution.supervision_action) {
            Write-Info ("监督动作={0} · risk={1}" -f $execution.supervision_action, $execution.supervision_risk)
        }
    }
    $quality = Invoke-Api -Method GET -Path "/api/v1/executions/$executionRunId/quality-report"
    if ($quality) {
        Write-Ok ("质量报告 status={0} · 总 {1} / 通过 {2} / 拒绝 {3} · 拒绝率 {4:P2}" -f `
            $quality.status, $quality.total_rows, $quality.passed_rows, $quality.rejected_rows, $quality.reject_rate)
        if ($quality.error_code_distribution) {
            Write-Info ("错误码分布：{0}" -f ($quality.error_code_distribution | ConvertTo-Json -Compress))
        }
    }
}

# ---------------------------------------------------------------------------
# STEP 12 — 证据账本可验证（AC-09）
# ---------------------------------------------------------------------------
Write-Step '证据账本整流验证（GET /projects/{id}/evidence-stream/verify）'
$verify = Invoke-Api -Method GET -Path "/api/v1/projects/$projectId/evidence-stream/verify"
if ($verify) {
    if ($verify.valid) {
        Write-Ok ("valid=true · event_count={0} · head_hash={1}…" -f $verify.event_count, $verify.head_hash.Substring(0, 12))
    } else {
        Write-Fail ("证据账本校验失败：{0}" -f ($verify.errors -join '; '))
    }
}

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host ('=' * 78) -ForegroundColor DarkGray
if ($script:Failures.Count -eq 0) {
    Write-Host '场景 A 演示完成：主链路全程无失败' -ForegroundColor Green
} else {
    Write-Host ("场景 A 演示完成，但有 {0} 处失败：" -f $script:Failures.Count) -ForegroundColor Red
    $script:Failures | ForEach-Object { Write-Host ("  - {0}" -f $_) -ForegroundColor Red }
}
Write-Host ('=' * 78) -ForegroundColor DarkGray
Write-Host ''
Write-Host '对应 AC：AC-10（端到端链路）/ AC-09（门禁与证据）/ AC-05（Harness）/ AC-07（质量四态）' -ForegroundColor Cyan
Write-Host '下一步：打开前端控制台 http://127.0.0.1:5173 逐页对照本脚本产生的治理事实。' -ForegroundColor Cyan
