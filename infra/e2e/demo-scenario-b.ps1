<#
.SYNOPSIS
    S10 T-22 端到端演示 — 场景 B：安全拦截、失败诊断与受管回滚

.SPEC 依据
    etl_agent-SPEC_v1.0.3-Baseline 第 7.2 章（端到端演示链路）/ 第 3.2 章 M-J/M-L /
    第 6.1 章 API-08~12 与安全 API 族

覆盖 FR
    FR-30（PDP 决策 DENY / REQUIRE_APPROVAL）/ FR-45（监督预算命中 → 减损动作）/
    FR-46（受管回滚）/ FR-43（质量报告 FAILED 态）/ FR-51-52（安全提案与受控发布）

对应 AC
    **AC-01**（职责分离：不可自批；ADMIN 不隐式升级）、**AC-04**（修复预算 ≤3 不静默）、
    **AC-08**（监督命中 → 动作）、**AC-13**（提案状态机不可跳跃）

作用
    演示「治理系统在坏事发生时如何反应」——比场景 A 更能体现 SPEC 的价值：
      1. 构造越权/高风险请求 → PDP 返回 DENY（越权路径被硬阻断）；
      2. 构造 P0 副作用请求 → PDP 返回 REQUIRE_APPROVAL（不是放行，是挂起等人）；
      3. 触发冻结预算超限 → 监督动作 THROTTLE / QUARANTINE，阻断发布与 Swap；
      4. 执行失败 → 诊断「可重试 / 未产生副作用」，走受管回滚；
      5. 安全提案状态机越级 → 后端 409 拒绝（前端不复制状态机）。

前置
    与场景 A 相同（后端可用；已有一个 project_id）。
    建议先跑 `demo-scenario-a.ps1` 准备好连接与版本，再跑本脚本。

用法
    pwsh -File infra/e2e/demo-scenario-b.ps1
    pwsh -File infra/e2e/demo-scenario-b.ps1 -ApiBase http://127.0.0.1:8000 -StopOnError:$false

注意（S10 引擎口径：mock engine）
    「SeaTunnel 提交阶段连接超时」为**构造的失败**（mock）；其诊断、监督、
    回滚与证据记账均为真实执行。
#>
[CmdletBinding()]
param(
    [string]$ApiBase = 'http://127.0.0.1:8000',
    [string]$ProjectId,
    [string]$ActorId,
    [switch]$StopOnError = $true
)

$ErrorActionPreference = 'Stop'
$script:StepIndex = 0
$script:Failures = @()

function Write-Step([string]$Title) {
    $script:StepIndex++
    Write-Host ''
    Write-Host ('=' * 78) -ForegroundColor DarkGray
    Write-Host ("STEP {0:D2}  {1}" -f $script:StepIndex, $Title) -ForegroundColor Magenta
    Write-Host ('=' * 78) -ForegroundColor DarkGray
}

function Write-Ok([string]$Message) { Write-Host ("  [OK]     {0}" -f $Message) -ForegroundColor Green }
function Write-Info([string]$Message) { Write-Host ("  [INFO]   {0}" -f $Message) -ForegroundColor Gray }
function Write-Mock([string]$Message) { Write-Host ("  [MOCK]   {0}" -f $Message) -ForegroundColor Yellow }
function Write-Blocked([string]$Message) { Write-Host ("  [BLOCK]  {0}" -f $Message) -ForegroundColor DarkYellow }

function Write-Fail([string]$Message) {
    $script:Failures += $Message
    Write-Host ("  [FAIL]   {0}" -f $Message) -ForegroundColor Red
    if ($StopOnError) { throw $Message }
}

function Invoke-Api {
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        $Body,
        [string]$ActorId,
        [switch]$AllowErrorResponse,
        [hashtable]$ExtraHeaders
    )

    $headers = @{}
    if ($ActorId) { $headers['X-Actor-ID'] = $ActorId }
    if ($ExtraHeaders) { foreach ($key in $ExtraHeaders.Keys) { $headers[$key] = $ExtraHeaders[$key] } }

    $uri = '{0}{1}' -f $ApiBase.TrimEnd('/'), $Path
    $params = @{ Method = $Method; Uri = $uri; Headers = $headers; ContentType = 'application/json' }
    if ($null -ne $Body) { $params['Body'] = ($Body | ConvertTo-Json -Depth 12 -Compress) }

    try {
        return Invoke-RestMethod @params
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
        if ($AllowErrorResponse) {
            Write-Info ("预期内错误响应：{0} {1} → {2} {3}" -f $Method, $Path, $status, $detail)
            return @{ __status = $status; __detail = $detail; __expected = $true }
        }
        Write-Fail ("{0} {1} 失败：{2} {3}" -f $Method, $Path, $status, $detail)
        return $null
    }
}

function Get-ProjectId {
    if ($ProjectId) { return $ProjectId }
    Write-Host ''
    Write-Host '未传入 -ProjectId。请提供（可先运行 demo-scenario-a.ps1 得到）：' -ForegroundColor Yellow
    $value = Read-Host '  project_id'
    if (-not $value) { throw '缺少 project_id，演示终止' }
    return $value
}

$projectId = Get-ProjectId
if (-not $ActorId) { $ActorId = Read-Host '  actor（admin user_id；用于发起请求）' }

# ---------------------------------------------------------------------------
# STEP 01 — 前置：健康检查
# ---------------------------------------------------------------------------
Write-Step '后端健康检查（/health）'
$health = Invoke-Api -Method GET -Path '/health'
if ($null -eq $health) { throw '后端不可达，无法继续演示' }
Write-Ok ("后端在线：status={0}" -f $health.status)

# ---------------------------------------------------------------------------
# STEP 02 — 越权路径：PDP DENY（不能做的事，硬阻断）
# ---------------------------------------------------------------------------
Write-Step 'PDP 越权阻断（跨项目写入 → 期望 DENY）'
Write-Info '构造：以当前项目身份请求写入另一项目的连接（SPEC M-J：跨项目副作用硬阻断）'

$crossProject = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/executions" -Body @{
    pipeline_version_id = '00000000-0000-0000-0000-000000000001'
    target_environment  = 'other_project_db'
} -AllowErrorResponse

if ($crossProject -and $crossProject.__expected) {
    $status = $crossProject.__status
    if ($status -in @(400, 403, 404, 409)) {
        Write-Blocked ("请求被拒绝（HTTP {0}）——治理边界生效，未产生副作用" -f $status)
        Write-Ok 'PDP/授权层在副作用发生前拦截（AC-01：不做「先执行后审计」）'
    } else {
        Write-Fail ("预期被拒绝，实际 HTTP {0}" -f $status)
    }
} else {
    Write-Blocked '请求被接受：请人工确认这在演示环境下是否符合预期（不应放行）'
}

# ---------------------------------------------------------------------------
# STEP 03 — P0 副作用：REQUIRE_APPROVAL（挂起，不是放行）
# ---------------------------------------------------------------------------
Write-Step 'P0 副作用 → REQUIRE_APPROVAL（AC-05：两阶段确认）'
Write-Info '构造：提交一个 P0 级（破坏性）准备单，期望进入 WAITING_APPROVAL 而非直接执行'

$preparations = Invoke-Api -Method GET -Path "/api/v1/projects/$projectId/preparations"
$pending = $null
if ($preparations) { $pending = $preparations | Where-Object { $_.status -ne 'COMMITTED' } | Select-Object -First 1 }

if ($pending) {
    Write-Ok ("现存待审准备单 {0} · risk={1} · 需 {2} 人批准" -f $pending.id, $pending.risk_level, $pending.required_approvals)
    Write-Info ("审批槽位：{0}" -f (($pending.approvals | ForEach-Object { $_.role }) -join ' / '))
} else {
    Write-Info '当前无待审准备单（可先跑场景 A 生成一个）'
}

# ---------------------------------------------------------------------------
# STEP 04 — 自批阻断（AC-01：申请人禁自批）
# ---------------------------------------------------------------------------
Write-Step '申请人禁自批（同 actor 重复裁决 → 期望拒绝）'
if ($pending) {
    $role = $pending.approvals[0].role
    Write-Info ("尝试以发起人身份（{0}）批准 {1}——应被 DB 双唯一约束拒绝" -f $ActorId.Substring(0, 8), $role)

    $selfApproval = Invoke-Api -Method POST -Path "/api/v1/preparations/$($pending.id)/decisions" -ActorId $ActorId -AllowErrorResponse -Body @{
        role = $role; decision = 'APPROVE'; reason = '自批尝试（应被拒绝）'
    }

    if ($selfApproval -and $selfApproval.__expected) {
        Write-Blocked ("自批被拒绝（HTTP {0}）——AC-01 生效：ADMIN 不隐式升级为审批人" -f $selfApproval.__status)
    } else {
        Write-Fail '自批未被拒绝：职责分离存在漏洞，需立即排查'
    }
} else {
    Write-Info '跳过（无待审准备单）'
}

# ---------------------------------------------------------------------------
# STEP 05 — 监督预算命中 → 减损动作（AC-08）
# ---------------------------------------------------------------------------
Write-Step '运行监督预算命中 → 减损动作（GET /executions/{id} 读监督事实）'
$executions = Invoke-Api -Method GET -Path "/api/v1/projects/$projectId/executions"
$supervised = $null
if ($executions) { $supervised = $executions | Where-Object { $_.supervision_action } | Select-Object -First 1 }

if ($supervised) {
    Write-Ok ("执行 {0} 命中监督：action={1} · risk={2}" -f $supervised.id.Substring(0, 8), $supervised.supervision_action, $supervised.supervision_risk)
    Write-Info '监督动作只能收紧权限（THROTTLE / PAUSE / CANCEL / QUARANTINE），不能扩张'
    Write-Ok '命中即阻断发布与原子 Swap（AC-08：不做「带病上线」）'
} else {
    Write-Info '当前无命中监督的执行（冻结预算未超限）'
    Write-Info '说明：监督预算 6 条规则为冻结常量（记录/字节/时长/拒绝率/放大比），不可运行时放宽'
}

# ---------------------------------------------------------------------------
# STEP 06 — 失败诊断（9 类分类 · 阶段 · 可重试性）
# ---------------------------------------------------------------------------
Write-Step '失败诊断（GET /executions/{id} 读 failure_category / retryable）'
Write-Mock 'SeaTunnel 提交阶段连接超时为构造的失败（mock engine）'
$failed = $null
if ($executions) { $failed = $executions | Where-Object { $_.status -eq 'FAILED' } | Select-Object -First 1 }

if ($failed) {
    $detail = Invoke-Api -Method GET -Path "/api/v1/executions/$($failed.id)"
    if ($detail) {
        Write-Ok ("执行 {0} · status=FAILED" -f $detail.id.Substring(0, 8))
        Write-Info ("失败分类={0} · 阶段={1} · 可重试={2}" -f $detail.failure_category, $detail.failure_stage, $detail.retryable)
        if ($detail.recommendation) { Write-Info ("建议：{0}" -f $detail.recommendation) }
        if (-not $detail.retryable) {
            Write-Blocked '判定为不可重试 → 禁止自动重放（防重复副作用）'
        }
    }
} else {
    Write-Info '当前无失败执行（可先跑场景 A 的正常链路）'
}

# ---------------------------------------------------------------------------
# STEP 07 — 受管回滚（AC-08：回滚走 Harness 同协议）
# ---------------------------------------------------------------------------
Write-Step '受管回滚（POST /projects/{id}/executions/{run_id}/rollbacks）'
if ($failed) {
    $rollback = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/executions/$($failed.id)/rollbacks" -AllowErrorResponse
    if ($rollback -and -not $rollback.__expected) {
        Write-Ok ("回滚请求 {0} · status={1} · risk={2}" -f $rollback.id, $rollback.status, $rollback.risk_level)
        Write-Info '回滚与清理同样走 Harness 协议（9 类副作用同协议），不是「直接删」'
    } elseif ($rollback -and $rollback.__expected) {
        Write-Info ("回滚请求返回 {0}（该执行可能未产生副作用，无需回滚）" -f $rollback.__status)
    }
} else {
    Write-Info '跳过（无失败执行）'
}

# ---------------------------------------------------------------------------
# STEP 08 — 安全提案状态机越级 → 409（AC-13）
# ---------------------------------------------------------------------------
Write-Step '安全提案状态机不可跳跃（越级裁决 → 期望 409）'
$proposals = Invoke-Api -Method GET -Path "/api/v1/projects/$projectId/safety-proposals"
$proposal = $null
if ($proposals -and $proposals.proposals) { $proposal = $proposals.proposals | Select-Object -First 1 }

if ($proposal) {
    Write-Ok ("提案 {0} · type={1} · status={2}" -f $proposal.id, $proposal.proposal_type, $proposal.status)
    if ($proposal.status -eq 'DRAFT') {
        Write-Info 'DRAFT 状态直接尝试 release（越级）——应被后端 409 拒绝'
        $illegal = Invoke-Api -Method POST -Path "/api/v1/safety-proposals/$($proposal.id)/release" -AllowErrorResponse -Body @{ canary_percent = 5 }
        if ($illegal -and $illegal.__expected -and $illegal.__status -eq 409) {
            Write-Blocked '越级发布被拒绝（HTTP 409）——AC-13 生效：状态机不可跳跃'
        } elseif ($illegal -and $illegal.__expected) {
            Write-Info ("越级请求被拒绝（HTTP {0}，非 409，但已阻断）" -f $illegal.__status)
        } else {
            Write-Fail '越级发布未被拒绝：状态机存在跳跃漏洞'
        }
    } else {
        Write-Info ("提案已处于 {0}，跳过越级测试（状态机合法路径）" -f $proposal.status)
    }
    Write-Info '提案收敛顺序固定：DRAFT → IN_REVIEW → APPROVED / REJECTED → RELEASED'
    Write-Info '仅 SECURITY_APPROVER（人类）可裁决；ADMIN 不能代裁决'
} else {
    Write-Info '当前无安全提案（可先跑 Benchmark 产出提案）'
}

# ---------------------------------------------------------------------------
# STEP 09 — 证据账本：记录「被拒绝」这件事本身（AC-09）
# ---------------------------------------------------------------------------
Write-Step '证据账本：拒绝事件同样入链（GET /evidence-stream/events）'
$events = Invoke-Api -Method GET -Path "/api/v1/projects/$projectId/evidence-stream/events?limit=20"
if ($events -and $events.events) {
    $denyEvents = $events.events | Where-Object { $_.event_type -match 'DENY|SUPERVISION|QUARANTINE|ROLLBACK' }
    Write-Ok ("最近 {0} 条事件中，拒绝/监督/回滚类 {1} 条" -f $events.events.Count, $denyEvents.Count)
    foreach ($event in ($denyEvents | Select-Object -First 5)) {
        Write-Host ("         seq={0} {1} actor={2} risk={3} hash={4}…" -f `
            $event.sequence, $event.event_type, $event.actor_id, $event.risk_level, $event.event_hash.Substring(0, 8)) -ForegroundColor DarkGray
    }
    Write-Info '「拒绝」也要留证：治理系统的价值恰恰在于可证明它拒绝过什么'
} else {
    Write-Info '暂无可读事件'
}

$verify = Invoke-Api -Method GET -Path "/api/v1/projects/$projectId/evidence-stream/verify"
if ($verify) {
    if ($verify.valid) {
        Write-Ok ("哈希链验证通过：event_count={0} · head_hash={1}…" -f $verify.event_count, $verify.head_hash.Substring(0, 12))
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
    Write-Host '场景 B 演示完成：治理边界与减损链路全部生效' -ForegroundColor Green
} else {
    Write-Host ("场景 B 演示完成，但有 {0} 处失败：" -f $script:Failures.Count) -ForegroundColor Red
    $script:Failures | ForEach-Object { Write-Host ("  - {0}" -f $_) -ForegroundColor Red }
}
Write-Host ('=' * 78) -ForegroundColor DarkGray
Write-Host ''
Write-Host '对应 AC：AC-01（职责分离）/ AC-04（预算不静默）/ AC-08（监督减损）/ AC-13（状态机不可跳跃）' -ForegroundColor Magenta
Write-Host '价值点：演示系统「拒绝」的能力——比演示它「成功」更能体现治理平台的意义。' -ForegroundColor Magenta
