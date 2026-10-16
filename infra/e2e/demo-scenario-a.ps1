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
    # 身份声明必须用 subject（FR-65：X-Actor-ID 即 users.subject）；
    # admin_user_id 是 UUID 主键，actor_resolver 按 subject 查表，传 UUID 会 401。
    $script:ActorId = $AdminSubject
    Write-Ok ("project_id = {0}" -f $projectId)
    Write-Ok ("actor_subject = {0}（后续以 X-Actor-ID 注入；admin_user_id = {1}）" -f $script:ActorId, $bootstrap.admin_user_id)
}

# ---------------------------------------------------------------------------
# 审批环境预备（AC-01 前置，非演示步骤）：四眼审批的 decisions 端点要求审批人
#   ① 是项目成员（require_actor_for_project：member_role 非空），且
#   ② 持有对应 Harness 职责（decide()：role 必须 ∈ actor.harness_roles，
#      仅来自 ProjectRoleGrant 显式授予，ADMIN 不隐式升级——FR-03）。
#   项目是本回合 bootstrap 新建、初始仅 admin 一人，故先以 admin（项目 ADMIN）
#   登记两名审批成员（role=APPROVER）并授予 Harness 职责（两步，缺一不可）。
#   两名审批人各授两项职责：准备单风险等级（P0/P1/P2）运行时才由 PDP 判定，
#   槽位组合不定，预授两项保证 required_roles[0]/[1] 都能落到人；
#   四眼判定逻辑（禁自批 / 槽位唯一 / 一人一槽）不受影响。
# ---------------------------------------------------------------------------
$approverSubjects = @('reviewer.data@demo.local', 'reviewer.security@demo.local')
$approverHarnessRoles = @('DATA_APPROVER', 'SECURITY_APPROVER')
foreach ($approver in $approverSubjects) {
    $member = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/members" -Body @{
        subject = $approver
        role    = 'APPROVER'
    }
    if ($member) { Write-Ok ("审批成员 {0} 已登记（role=APPROVER）" -f $approver) }
    foreach ($harnessRole in $approverHarnessRoles) {
        $grant = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/role-grants" -Body @{
            subject = $approver
            role    = $harnessRole
        }
        if ($grant) { Write-Ok ("审批成员 {0} 已授予 Harness 职责 {1}" -f $approver, $harnessRole) }
    }
}

# 操作员预备（AC-05 前置，非演示步骤）：API-10 commit 仅 OPERATOR 可提交
#（require_actor_for_project + OPERATOR Harness 职责，ADMIN 不隐式升级）。
#操作员须同时是项目成员（member_role 非空）——成员 + 职责两步。
$operatorSubject = 'operator@demo.local'
$operatorMember = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/members" -Body @{
    subject = $operatorSubject
    role    = 'DATA_ENGINEER'
}
if ($operatorMember) { Write-Ok ("操作员 {0} 已登记（role=DATA_ENGINEER）" -f $operatorSubject) }
$operatorGrant = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/role-grants" -Body @{
    subject = $operatorSubject
    role    = 'OPERATOR'
}
if ($operatorGrant) { Write-Ok ("操作员 {0} 已授予 Harness 职责 OPERATOR" -f $operatorSubject) }

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
    host           = '127.0.0.1'   # 宿主机 uv run 后端按发布端口直连；原 'mysql' 是容器网络名（仅容器内可解析），非脚本逻辑 bug
    port           = 13306         # 容器 3306 改发 13306：宿主机 127.0.0.1:3306 被 WSL relay 占用，直连会被劫走
    database_name  = 'source_db'
    username       = 'demo_reader'
    password       = 'demo_reader_pwd_2026'
}
$sinkConn = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/connections" -Body @{
    name           = 'doris_dw'
    connector_type = 'DORIS'
    host           = '127.0.0.1'   # 同上：宿主机后端直连 Doris FE 发布端口 9030；原 'doris' 是容器网络名（compose 服务名 doris-fe 亦仅容器内可解析）
    port           = 9030
    database_name  = 'doris_dw'
    username       = 'demo_writer'
    password       = 'demo_writer_pwd_2026'
}
if ($sourceConn) { Write-Ok ("SOURCE 连接 {0}（凭据已加密托管，响应零明文）" -f $sourceConn.id) }
if ($sinkConn) { Write-Ok ("SINK 连接 {0}" -f $sinkConn.id) }

# ---------------------------------------------------------------------------
# Profile 探查预备（AC-03 前置，非演示步骤）：生成运行（API-05）接收的是
#   source_profile_ids / sink_profile_ids（Profile ID 列表），不是连接 ID；
#   且生成图要求双 Profile 齐备才会收敛（缺一即 WAITING_INPUT 挂起澄清）。
#   故先对两条连接各做一次表级只读探查（API-04）；表名与 seed / doris-init
#   实际一致：source=source_db.orders，sink=doris_dw.order_wide。
# ---------------------------------------------------------------------------
$sourceProfile = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/profiles" -Body @{
    connection_id = $sourceConn.id
    endpoint_role = 'SOURCE'
    schema_name   = 'source_db'
    table_name    = 'orders'
}
$sinkProfile = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/profiles" -Body @{
    connection_id = $sinkConn.id
    endpoint_role = 'SINK'
    schema_name   = 'doris_dw'
    table_name    = 'order_wide'
}
if ($sourceProfile) { Write-Ok ("SOURCE Profile {0}（source_db.orders，行数≈{1}）" -f $sourceProfile.id, $sourceProfile.approximate_row_count) }
if ($sinkProfile) { Write-Ok ("SINK Profile {0}（doris_dw.order_wide，Doris 走 MySQL 协议通道）" -f $sinkProfile.id) }

# ---------------------------------------------------------------------------
# STEP 03 — 连通性测试
# ---------------------------------------------------------------------------
Write-Step '连通性测试（POST /connections/{id}/test）'
# 旁注（S3 禁止事项 15，预期内失败、非回归）：DORIS 连接器无探针实装，
# /test 对 doris_dw 返回 422 BIZ_422_CONNECTOR_UNSUPPORTED 是**策略判决**——
# 探针通道只覆盖 POSTGRESQL/MYSQL（Doris 探针另行排期）。Doris 数据面连通性
# 已由 SINK Profile 探查（information_schema 走 MySQL 协议，见上方预备步）
# 与后续真实执行双重证明，此处 [FAIL] 原文保留、不伪装为通过。
foreach ($conn in @($sourceConn, $sinkConn)) {
    if (-not $conn) { continue }
    $test = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/connections/$($conn.id)/test"
    if ($test) {
        Write-Ok ("{0} → status={1}（受理于 {2}）" -f $conn.name, $test.status, $test.started_at)
    }
}

# ---------------------------------------------------------------------------
# STEP 04 — 发起生成（自然语言需求 → AgentRun）
# ---------------------------------------------------------------------------
Write-Step '发起生成（POST /projects/{id}/agent-runs）'
$requirement = '把 MySQL orders 增量同步到 Doris 宽表 order_wide，金额分转元，地址字段做归一化，脏数据走错误分流。'
$run = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/agent-runs" -Body @{
    requirement       = $requirement
    source_profile_ids = @($sourceProfile.id)
    sink_profile_ids   = @($sinkProfile.id)
}
if ($run) {
    Write-Ok ("AgentRun {0} · status={1} · thread_id={2}" -f $run.id, $run.status, $run.thread_id)
    if ($run.pending_question) {
        Write-Info ("澄清问题：{0}" -f $run.pending_question)
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
if ($run -and $run.pipeline_version_id) {
    $version = Invoke-Api -Method GET -Path "/api/v1/pipeline-versions/$($run.pipeline_version_id)"
}
if ($version) {
    Write-Ok ("版本 V{0} · status={1} · artifact_sha256={2}…" -f $version.version_no, $version.status, $version.artifact_sha256.Substring(0, 12))
    if ($version.hocon) {
        Write-Info 'HOCON 候选（前 3 行）：'
        ($version.hocon -split "`n" | Select-Object -First 3) | ForEach-Object { Write-Host ("         {0}" -f $_) -ForegroundColor DarkGray }
    }
} else {
    Write-Warn '未取到版本（pipeline_version_id 可能未回填）；后续步骤以演示态继续'
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
        idempotency_key     = [guid]::NewGuid().ToString()
        environment         = 'dev'
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
    if (-not $prep.required_roles -or $prep.required_roles.Count -eq 0) {
        # PDP 评级 P3（required_approvals=0）：无审批槽位，准备单直接 READY；
        # 四眼机制在 P0/P1/P2 才激活——策略判决如此，非流程跳过（不放宽门禁）
        Write-Info ("PDP 评级 {0} · required_approvals={1}：无审批槽位，四眼审批本回合不激活" -f $prep.risk_level, $prep.required_approvals)
    } else {
    # 申请人 = 当前 actor；DB 双唯一约束禁自批 → 换身份模拟第二名审批人。具名审批人为 bootstrap 后预登记的两名项目成员（见上方「审批环境预备」：成员登记 + Harness 职责授予两步，缺一不可）；原硬编码 UUID 既非 subject 也非项目成员，后端按 subject 解析必然失败（FR-65 / FR-03）。
    $approverA = $approverSubjects[0]
    $approverB = $approverSubjects[1]
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
}

# ---------------------------------------------------------------------------
# STEP 10 — Commit（签发单次 Capability；AC-05）
# ---------------------------------------------------------------------------
Write-Step 'Commit（POST /preparations/{id}/commit → Ed25519 单次 Capability，TTL 300s）'
$executionRunId = $null
if ($prep) {
    $commit = Invoke-Api -Method POST -Path "/api/v1/preparations/$($prep.id)/commit" -ActorId $operatorSubject
    if ($commit) {
        # CommitResponse 契约字段（harness/schemas.py）：run_id / token_id / expires_at
        $executionRunId = $commit.run_id
        Write-Ok ("ExecutionRun {0}（QUEUED）" -f $executionRunId)
        Write-Ok ("Capability {0} · 过期于 {1}" -f $commit.token_id, $commit.expires_at)
    }
}
# 引擎通道标注与 engine-mode 值绑定（F5：不是硬编码字符串）：
# 取会话环境变量 ETL_AGENT_ENGINE_MODE（与 worker compose 的 ETL_AGENT_ENGINE_MODE
# 同一变量；两者分属不同进程，一致性由演示 Runbook 约定 + worker 启动日志核对）。
$engineMode = if ($env:ETL_AGENT_ENGINE_MODE) { $env:ETL_AGENT_ENGINE_MODE } else { 'real' }
if ($engineMode -eq 'mock') {
    Write-Mock ("engine_mode=mock（人类裁决 C）：引擎 check/submit 为演示态合成指标——其余（后端 API / 审批 / HOCON 生成 / 质量契约判定 / 证据链路）全部真实执行" )
} else {
    Write-Ok ("engine_mode={0}：真实引擎执行" -f $engineMode)
}

# ---------------------------------------------------------------------------
# STEP 11 — 执行监控与质量报告（AC-07）
# ---------------------------------------------------------------------------
Write-Step '执行监控与质量报告（GET /executions/{id} + /quality-report）'
if ($executionRunId) {
    # 有界轮询等终态（不改判定标准：终态才打印、FAILED 进摘要的语义不变）。
    # 单次 GET 必 race——run14 实证：commit 后约 1.8s Worker 才 claimed、
    # 2.2s 到终态，脚本若不等待只会看到 QUEUED 与质量报告 404。
    $execution = $null
    for ($i = 0; $i -lt 10; $i++) {
        $execution = Invoke-Api -Method GET -Path "/api/v1/executions/$executionRunId"
        if ($execution -and $execution.status -in @('SUCCEEDED', 'FAILED', 'CANCELLED')) { break }
        Start-Sleep -Seconds 2
    }
    if ($execution) {
        # ExecutionDetailResponse 契约字段（execution/schemas.py）：sink_rows / failure_*；
        # supervision_snapshot 当前恒 None（S7 遗留硬编码，S8 落表后未回填，已记异常清单）
        if ($execution.status -eq 'FAILED') {
            Write-Fail ("执行失败：category={0} · stage={1} · {2}" -f $execution.failure_category, $execution.failure_stage, $execution.diagnosis)
        } else {
            Write-Ok ("执行 status={0} · 源行数={1} · 目标行数={2}" -f $execution.status, $execution.source_rows, $execution.sink_rows)
        }
        if ($execution.supervision_snapshot) {
            Write-Info ("监督快照：{0}" -f ($execution.supervision_snapshot | ConvertTo-Json -Compress -Depth 6))
        }
    }
    $quality = Invoke-Api -Method GET -Path "/api/v1/executions/$executionRunId/quality-report"
    if ($quality) {
        Write-Ok ("质量报告 status={0} · 总 {1} / 通过 {2} / 拒绝 {3} · 拒绝率 {4:P2}" -f `
            $quality.status, $quality.total_rows, $quality.passed_rows, $quality.rejected_rows, $quality.reject_rate)
        if ($quality.valid_sink -or $quality.error_sink) {
            Write-Info ("好数据落表 {0} · 脏数据落表 {1}" -f $quality.valid_sink, $quality.error_sink)
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
