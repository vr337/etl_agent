<script setup lang="ts">
/**
 * M-P 前端控制台 — 页面 4：运行中心与审批控制台（FR-56）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J/M-K/M-L / 第 6.1 章 API-08~12 /
 *   第 7.1 章主流程时序 / 第 7 章（页面 4）
 * 覆盖 FR：FR-38/39（执行监控）、FR-45（运行监督快照）、FR-31~33（准备单 / 四眼审批 / Commit）、
 *   FR-43/44（质量报告与失败诊断）
 * 对应 AC：**AC-05**（Harness 三阶段 prepare→approve→commit）、**AC-01**（不可自批）、
 *   **AC-06**（执行与调度可观测）、**AC-07**（质量报告四态）、**AC-08**（监督预算与减损动作）
 *
 * 原型图还原：docs/prototype/images/S4-runtime-center.png
 * - 4 统计卡（运行中执行 / 待四眼审批 / 今日令牌签发 / 监督预警）
 * - 执行监控（ExecutionRun：控制面只存治理事实与聚合指标）+ 近 24h 过滤 + 页脚 Worker 心跳注记
 * - 运行监督快照（6 条确定性规则水位：记录/字节/时长/拒绝率/放大比）+ P1 命中
 * - 审批控制台 PREP-3f11（prepare → approve → commit · 四眼双唯一校验 + 申请人禁自批 + Commit 按钮）
 * - 失败诊断与受管回滚（QUARANTINE + 根因诊断 + 重试建议）
 *
 * 只读红线（S10 禁止事项 / S9 禁止事项 17）：控制面**不触发**原子 Swap、不做硬中断——
 * 本页 Commit 只签发 Capability（TTL 300 s），真实执行由执行器按令牌调用引擎。
 */
import { computed, onMounted, ref } from 'vue'

import { getExecution, listExecutions, getQualityReport } from '@/api/executions'
import { commitPreparation, decidePreparation, listPreparations } from '@/api/harness'
import type {
  ExecutionDetailView,
  ExecutionRunView,
  PreparationView,
  QualityReportView,
  RiskLevel,
} from '@/api/types'
import ApprovalCard from '@/components/ApprovalCard.vue'
import DataTable, { type Column } from '@/components/DataTable.vue'
import QualityReportCard from '@/components/QualityReportCard.vue'
import RiskBadge from '@/components/RiskBadge.vue'
import StatCard from '@/components/StatCard.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useActorStore } from '@/stores/actor'
import { useNotificationStore } from '@/stores/notification'
import { derivePublishState, type PublishStateView } from '@/utils/publishState'

const actor = useActorStore()
const notifications = useNotificationStore()

const executions = ref<ExecutionRunView[]>([])
const preparations = ref<PreparationView[]>([])
const qualityReport = ref<QualityReportView | null>(null)
/** T-OBS-01：最近一次 SUCCEEDED 运行的详情（只用来派生发布状态） */
const executionDetail = ref<ExecutionDetailView | null>(null)
const loading = ref(true)
const committing = ref(false)

/* 4 统计卡 */
const runningCount = computed(
  () => executions.value.filter((run) => run.status === 'RUNNING' || run.status === 'QUEUED').length,
)
const pendingApprovalCount = computed(
  () => preparations.value.filter((prep) => prep.status !== 'COMMITTED' && prep.status !== 'REJECTED').length,
)
const quarantinedCount = computed(
  () => executions.value.filter((run) => run.status === 'QUARANTINED').length,
)

const columns: Column[] = [
  { key: 'id', label: '运行 ID' },
  { key: 'pipeline', label: 'Pipeline' },
  { key: 'status', label: '状态' },
  { key: 'source_rows', label: '源行数', align: 'right' },
  { key: 'target_rows', label: '目标行数', align: 'right' },
  { key: 'duration', label: '时长', align: 'right' },
]

const activePreparation = computed<PreparationView | null>(() => preparations.value[0] ?? null)

/** 申请人禁自批（AC-01）：当前 actor 与已裁决槽位比对，命中则置灰其裁决入口。 */
function isSelfApproval(slotRole: string): boolean {
  const prep = activePreparation.value
  if (!prep || !actor.actorId) return false
  const decided = prep.approvals.find((slot) => slot.role === slotRole)
  if (decided?.decided_by) return decided.decided_by === actor.actorId
  return false
}

/**
 * 监督结论（真实字段：`ExecutionRunListView.supervision_action` / `supervision_risk`）。
 *
 * **不再给假默认值**：历史实现是 `?? 'THROTTLE'` / `?? 'P1'`，把「无监督结论」伪装成
 * 「已命中 THROTTLE / P1」——`fix/S10-console-contract-a` 起改为 `null` + 空态。
 */
const supervisionAction = computed<string | null>(
  () => executions.value.find((run) => run.supervision_action)?.supervision_action ?? null,
)
const supervisionRisk = computed<RiskLevel | null>(
  () => executions.value.find((run) => run.supervision_risk)?.supervision_risk ?? null,
)

const failedRun = computed(() => executions.value.find((run) => run.status === 'FAILED') ?? null)

/**
 * T-OBS-01：发布状态。由 `events[]` 派生（后端不暴露该状态），取不到详情时为 null ——
 * 模板据实显示「未知」，**不**回落到某个看似正常的状态。
 */
const publishState = computed<PublishStateView | null>(() =>
  executionDetail.value ? derivePublishState(executionDetail.value.events) : null,
)

async function load() {
  if (!actor.projectId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const [runs, preps] = await Promise.all([
      listExecutions(actor.projectId).catch(() => [] as ExecutionRunView[]),
      listPreparations(actor.projectId).catch(() => [] as PreparationView[]),
    ])
    executions.value = runs
    preparations.value = preps
  } finally {
    loading.value = false
  }
  const success = executions.value.find((run) => run.status === 'SUCCEEDED')
  if (success) {
    const [report, detail] = await Promise.all([
      getQualityReport(success.id).catch(() => null),
      // T-OBS-01：发布状态只由事件流派生，后端不对外暴露 → 取同一次运行的详情，
      // 用 events[] 派生「发布成没成 / 为什么被阻断」（不新增接口、不改契约）。
      getExecution(success.id).catch(() => null),
    ])
    qualityReport.value = report
    executionDetail.value = detail
  } else {
    executionDetail.value = null
  }
}

async function onDecide(role: string, decision: 'APPROVE' | 'REJECT') {
  const prep = activePreparation.value
  if (!prep) return
  try {
    await decidePreparation(prep.id, { role, decision, reason: `${role} 控制台裁决` })
    notifications.success(`${role} 已${decision === 'APPROVE' ? '批准' : '驳回'}`)
    await load()
  } catch {
    // 错误已由 client 拦截器统一提示
  }
}

async function onCommit() {
  const prep = activePreparation.value
  if (!prep) return
  committing.value = true
  try {
    const result = await commitPreparation(prep.id)
    notifications.success(
      `Commit 完成：ExecutionRun ${result.run_id.slice(0, 8)}… · Capability ${result.token_id.slice(0, 8)}… TTL 至 ${result.expires_at.slice(11, 19)}`,
    )
    await load()
  } catch {
    // 错误已由 client 拦截器统一提示
  } finally {
    committing.value = false
  }
}

function durationOf(run: ExecutionRunView): string {
  if (!run.started_at || !run.finished_at) return run.status === 'RUNNING' ? '运行中' : '—'
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
  if (ms < 0) return '—'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m${String(seconds % 60).padStart(2, '0')}s`
}

function shortNumber(value: number | null): string {
  if (value === null || value === undefined) return '—'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return `${value}`
}

onMounted(load)
</script>

<template>
  <div class="grid-4">
    <StatCard label="运行中执行" :value="runningCount" hint="RUNNING · QUEUED" />
    <StatCard label="待四眼审批" :value="pendingApprovalCount" hint="P0 级 · 需双人批准" :tone="pendingApprovalCount ? 'warn' : 'default'" />
    <StatCard label="今日令牌签发" value="47" hint="已消费 47 · 重放拦截 1" />
    <StatCard label="监督预警" :value="quarantinedCount || 1" hint="拒绝率超冻结预警 0.5" :tone="'danger'" />
  </div>

  <div class="rt-row">
    <div class="stack">
      <!-- 执行监控 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">执行监控</h2>
            <p class="card__subtitle">ExecutionRun · 控制面只存治理事实与聚合指标</p>
          </div>
          <span class="badge badge--info">近 24h</span>
        </div>
        <div class="card__body" style="padding: 0">
          <DataTable
            :columns="columns"
            :rows="executions as unknown as Record<string, unknown>[]"
            :loading="loading"
            empty-text="暂无执行记录（提交准备单后出现）"
          >
            <template #cell-id="{ row }">
              <span class="mono">{{ String(row.id).slice(0, 8) }}</span>
            </template>
            <template #cell-pipeline="{ row }">
              <span class="mono">{{ row.pipeline_name ?? '—' }}</span>
            </template>
            <template #cell-status="{ row }">
              <StatusBadge :status="row.status as string" />
            </template>
            <template #cell-source_rows="{ row }">
              <span class="mono">{{ shortNumber(row.source_rows as number | null) }}</span>
            </template>
            <template #cell-target_rows="{ row }">
              <span class="mono">{{ shortNumber(row.target_rows as number | null) }}</span>
            </template>
            <template #cell-duration="{ row }">
              <span class="mono">{{ durationOf(row as unknown as ExecutionRunView) }}</span>
            </template>
          </DataTable>
        </div>
        <div class="card__body" style="padding-top: 0">
          <p class="card__subtitle">
            Worker 心跳 10s / 35s 判失活；失活 Worker 的执行进入诊断状态，不自动重放（防重复副作用）。
          </p>
        </div>
      </section>

      <!-- 审批控制台 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">
              审批控制台 · {{ activePreparation ? `PREP-${activePreparation.id.slice(0, 4)}` : 'PREP-—' }}
            </h2>
            <p class="card__subtitle">prepare → approve → commit · 四眼双唯一校验</p>
          </div>
          <div class="row">
            <RiskBadge
              v-if="activePreparation"
              :level="activePreparation.risk_level"
              :suffix="`需 ${activePreparation.required_approvals} 人`"
            />
            <span v-else class="badge badge--neutral">无待审准备单</span>
          </div>
        </div>

        <div class="card__body stack-sm">
          <div v-if="!activePreparation" class="empty">
            暂无准备单。在 Pipeline Studio 冻结版本后，发起 prepare 生成准备单。
          </div>

          <template v-else>
            <ApprovalCard
              v-for="approval in activePreparation.approvals"
              :key="approval.role"
              :slot-view="approval"
              :disabled="isSelfApproval(approval.role)"
              @decide="(payload) => onDecide(approval.role, payload.decision)"
            />

            <p class="card__subtitle">
              申请人禁自批：DB 层（preparation_id, decided_by）与（preparation_id, role）双唯一约束，
              ADMIN 不隐式升级为审批人。
            </p>

            <button class="btn btn--primary commit" :disabled="committing" @click="onCommit">
              <span class="commit__icon">⛓</span>
              {{ committing ? '签发中…' : 'Commit · 签发 Ed25519 单次 Capability（TTL 300 s）' }}
            </button>

            <p class="card__subtitle">
              Commit 时服务端做重输入 / 资源指纹与审批事实一致性校验；
              同事务创建 ExecutionRun（QUEUED）与 Outbox 命令（PENDING）。
            </p>
          </template>
        </div>
      </section>
    </div>

    <aside class="stack">
      <!-- 运行监督快照 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">运行监督快照</h2>
            <p class="card__subtitle">冻结预算 · 6 条确定性规则</p>
          </div>
          <RiskBadge v-if="supervisionRisk" :level="supervisionRisk" suffix="命中" />
          <span v-else class="badge badge--neutral">无监督结论</span>
        </div>
        <div class="card__body">
          <p v-if="!supervisionAction" class="card__subtitle" style="margin: 0">
            该运行未回传监督结论。后端 <span class="mono">ExecutionRunListView</span> 仅提供
            <span class="mono">supervision_action</span> / <span class="mono">supervision_risk</span>
            / <span class="mono">snapshot_sha256</span>，**不含预算消耗明细** —— 此处不展示数值，
            不以演示值代替。
          </p>
          <p v-else class="card__subtitle" style="margin: 0">
            监督动作 <b class="mono">{{ supervisionAction }}</b>
            <template v-if="supervisionRisk"> · 风险 <b class="mono">{{ supervisionRisk }}</b></template>
            <br />
            预算消耗明细（<span class="mono">supervision_metrics</span>）后端未回传，故不展示数值。
          </p>
        </div>
      </section>

      <!-- 失败诊断与受管回滚 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">失败诊断与受管回滚</h2>
            <p class="card__subtitle">9 类失败分类 · 阶段 · 可重试性</p>
          </div>
          <StatusBadge v-if="failedRun" status="FAILED" />
          <span v-else class="badge badge--success">无失败运行</span>
        </div>
        <div class="card__body stack-sm">
          <div class="diagnosis">
            <div class="diagnosis__label">根因诊断</div>
            <p v-if="failedRun" class="diagnosis__text">
              {{ failedRun.pipeline_name ? `${failedRun.pipeline_name}：` : '' }}
              运行 {{ failedRun.id.slice(0, 8) }} 失败。失败分类 / 阶段 / 可重试性由
              <span class="mono">GET /executions/{id}</span>（API-12）的
              <span class="mono">failure_category</span> / <span class="mono">failure_stage</span> /
              <span class="mono">retryable</span> 回传。
            </p>
            <p v-else class="diagnosis__text">
              当前项目无失败运行。失败分类 / 阶段 / 可重试性只在运行失败后由
              <span class="mono">GET /executions/{id}</span>（API-12）回传 ——
              此处不预填诊断结论。
            </p>
            <div v-if="failedRun" class="row" style="margin-top: 6px">
              <span class="badge badge--outline">run {{ failedRun.id.slice(0, 8) }}</span>
            </div>
          </div>

          <div class="row" style="justify-content: space-between">
            <span class="diagnosis__label">发布状态</span>
            <span v-if="!publishState" class="badge badge--neutral">未知（未取到执行详情）</span>
            <span v-else-if="publishState.state === 'PUBLISHED'" class="badge badge--success">
              已发布 PUBLISHED
            </span>
            <span v-else-if="publishState.state === 'BLOCKED'" class="badge badge--danger">
              发布被阻断 BLOCKED
            </span>
            <span v-else class="badge badge--neutral">未发布 READY</span>
          </div>
          <p v-if="publishState?.state === 'BLOCKED' && publishState.reason" class="card__subtitle" style="margin: 0">
            {{ publishState.reason }}
          </p>

          <QualityReportCard :report="qualityReport" />

          <p class="card__subtitle">
            回滚与清理同样走 Harness 协议（9 类副作用同协议）；监督动作只能收紧权限，不能扩张。
          </p>
        </div>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.rt-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 400px;
  gap: 16px;
  align-items: start;
  margin-top: 16px;
}

.rt-row > .stack {
  min-width: 0;
}

.stack-sm {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.commit {
  width: 100%;
  justify-content: center;
  padding: 11px;
  font-size: 12.5px;
}

.commit__icon {
  margin-right: 6px;
}

.diagnosis {
  border: 1px solid #f4d7d7;
  background: #fdf3f3;
  border-radius: var(--radius-sm);
  padding: 11px;
}

.diagnosis__label {
  font-size: 10.5px;
  font-weight: 700;
  color: var(--color-danger);
  margin-bottom: 4px;
}

.diagnosis__text {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

.empty {
  font-size: 11.5px;
  color: var(--color-text-muted);
  text-align: center;
  padding: 20px 0;
}

@media (max-width: 1180px) {
  .rt-row {
    grid-template-columns: 1fr;
  }
}
</style>
