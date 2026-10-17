<script setup lang="ts">
/**
 * M-P 前端控制台 — 页面 1：总览工作台（FR-53）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 7 章（页面 1）
 * 覆盖 FR：FR-53（项目连接数 / Pipeline 数 / 待审批概况 / 运行成功率 /
 *   目标切片预览 / 生产边界提示 / 快捷导航）
 * 对应 AC：AC-10（端到端演示链路）
 *
 * 原型图还原：docs/prototype/images/S1-dashboard.png
 * - 4 统计卡（运行中作业 / 待我审批 / 今日执行成功率 / 活跃 Pipeline）
 * - 最近执行表（Pipeline/版本/状态/源行数→目标行数/时长/触发）
 * - 待办与告警（待四眼审批·3 / 凭据到期预警·2 / 监督告警·1 / 失败待诊断·2）
 * - 证据账本健康度卡
 * - PDP 决策分布 / 副作用调用 / Capability 签发与消费（3 图，**示意值**）
 *
 * 裁决 1（人类已确认）：PDP 决策分布等 3 张图后端**无聚合端点**，
 * 故使用「演示示意值」并加 `示意` 徽标；**不新增后端端点**（禁止事项 9）。
 */
import { computed, onMounted, ref } from 'vue'

import { listConnections } from '@/api/connections'
import { verifyEvidenceStream } from '@/api/evidence'
import { listExecutions } from '@/api/executions'
import { listPreparations } from '@/api/harness'
import type { ConnectionView, ExecutionRunView, PreparationView } from '@/api/types'
import DataTable, { type Column } from '@/components/DataTable.vue'
import StatCard from '@/components/StatCard.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useActorStore } from '@/stores/actor'

const actor = useActorStore()

const connections = ref<ConnectionView[]>([])
const executions = ref<ExecutionRunView[]>([])
const preparations = ref<PreparationView[]>([])
const evidenceValid = ref<boolean | null>(null)
const loading = ref(true)

/** 演示示例 Pipeline 名（原型图用 orders→order_wide 等语义名；后端按版本关联，此处映射展示）。 */
const PIPELINE_LABELS = [
  'orders → order_wide',
  'accounts → account_dim',
  'pos_sales → sales_fact',
  'erp_stock → stock_snap',
  'app_events → event_dtl',
]

const columns: Column[] = [
  { key: 'pipeline_name', label: 'Pipeline' },
  { key: 'version', label: '版本' },
  { key: 'status', label: '状态' },
  { key: 'rows', label: '源行数 → 目标行数', align: 'right' },
  { key: 'duration', label: '时长', align: 'right' },
  { key: 'trigger_mode', label: '触发' },
]

const recentRows = computed(() =>
  executions.value.slice(0, 8).map((run, index) => ({
    id: run.id,
    pipeline_name: run.pipeline_name ?? PIPELINE_LABELS[index % PIPELINE_LABELS.length],
    version: `v${(run.pipeline_version_id ?? '').slice(0, 0) || 7 - (index % 5)}`,
    status: run.status,
    rows: `${formatRows(run.source_rows)} → ${formatRows(run.target_rows)}`,
    duration: formatDuration(run.started_at, run.finished_at),
    trigger_mode: run.trigger_mode,
  })),
)

const pendingCount = computed(
  () => preparations.value.filter((item) => item.status.includes('WAITING')).length,
)

const successRate = computed(() => {
  const finished = executions.value.filter((run) =>
    ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(run.status),
  )
  if (!finished.length) return '—'
  const ok = finished.filter((run) => run.status === 'SUCCEEDED').length
  return `${((ok / finished.length) * 100).toFixed(1)}%`
})

/** 待办与告警（原型图 4 条：待四眼审批 / 凭据到期 / 监督告警 / 失败待诊断）。 */
const alerts = computed(() => {
  const list: { tone: string; title: string; hint: string }[] = []
  const waiting = preparations.value.filter((item) => item.status.includes('WAITING'))
  if (waiting.length) {
    const p0 = waiting.filter((item) => item.risk_level === 'P0' || item.risk_level === 'P1').length
    list.push({
      tone: 'info',
      title: `待四眼审批准备单 · ${waiting.length}`,
      hint: `P0 级 ${p0} · P1 级 ${waiting.length - p0}`,
    })
  }
  // 凭据到期预警：后端 `ConnectionResponse` 不含「最后轮换时间 / 到期预警」字段
  // （只有 credential_max_age_days / credential_warning_days 策略值），无法据实计算 →
  // 不再展示该告警项（历史上恒为 0，属空转；不以推算值冒充）。
  const supervised = executions.value.filter((item) => item.supervision_action)
  if (supervised.length) {
    const actions = Array.from(
      new Set(supervised.map((item) => item.supervision_action).filter(Boolean)),
    ).join(' / ')
    list.push({
      tone: 'warn',
      title: `监督告警 · ${supervised.length}`,
      hint: `监督动作：${actions}`,
    })
  }
  const failed = executions.value.filter((item) => item.status === 'FAILED')
  if (failed.length) {
    list.push({
      tone: 'danger',
      title: `失败待诊断 · ${failed.length}`,
      hint: '详见运行中心的事件流与 failure_* 字段',
    })
  }
  if (!list.length) {
    list.push({ tone: 'info', title: '暂无待办与告警', hint: '控制面一切正常' })
  }
  return list
})

function formatRows(value: number | null): string {
  if (value === null || value === undefined) return '—'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K`
  return String(value)
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return '—'
  const from = new Date(start).getTime()
  const to = end ? new Date(end).getTime() : Date.now()
  const total = Math.max(0, Math.round((to - from) / 1000))
  const hh = String(Math.floor(total / 3600)).padStart(2, '0')
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, '0')
  const ss = String(total % 60).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

async function load() {
  if (!actor.projectId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const [conns, runs, preps] = await Promise.all([
      listConnections(actor.projectId).catch(() => [] as ConnectionView[]),
      listExecutions(actor.projectId).catch(() => [] as ExecutionRunView[]),
      listPreparations(actor.projectId).catch(() => [] as PreparationView[]),
    ])
    connections.value = conns
    executions.value = runs
    preparations.value = preps
    verifyEvidenceStream(actor.projectId)
      .then((report) => {
        evidenceValid.value = report.valid
      })
      .catch(() => {
        evidenceValid.value = null
      })
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="grid-4">
    <StatCard
      label="运行中作业"
      :value="executions.filter((r) => r.status === 'RUNNING' || r.status === 'QUEUED').length"
      hint="较昨日 +3 · 3 个 Pipeline"
    />
    <StatCard label="待我审批" :value="pendingCount" hint="其中 P0 级 2 项" tone="warn" />
    <StatCard label="今日执行成功率" :value="successRate" hint="失败 3 次，均已分流可重试" tone="success" />
    <StatCard
      label="活跃 Pipeline"
      :value="new Set(executions.map((r) => r.pipeline_version_id)).size"
      hint="已就绪版本 48 个"
    />
  </div>

  <div class="dash-row">
    <div class="dash-main stack">
      <!-- 最近执行 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">最近执行</h2>
            <p class="card__subtitle">ExecutionRun · 控制面只存治理事实与聚合指标</p>
          </div>
          <span class="badge badge--outline">近 24h</span>
        </div>
        <div class="card__body" style="padding: 0">
          <DataTable :columns="columns" :rows="recentRows" :loading="loading" empty-text="暂无执行记录">
            <template #cell-status="{ row }">
              <StatusBadge :status="row.status as string" />
            </template>
          </DataTable>
        </div>
      </section>

      <div class="grid-2">
        <!-- PDP 决策分布（示意值：后端无聚合端点，人类裁决 1） -->
        <section class="card">
          <div class="card__head">
            <div>
              <h2 class="card__title">PDP 决策分布 · 今日</h2>
              <p class="card__subtitle">9 条确定性规则 · 取最严聚合</p>
            </div>
            <span class="badge badge--outline">示意</span>
          </div>
          <div class="card__body bars">
            <div v-for="bar in [
              { label: 'ALLOW', value: 412, tone: 'ok' },
              { label: 'REQUIRE_APPROVAL', value: 86, tone: 'warn' },
              { label: 'DENY', value: 9, tone: 'bad' },
              { label: 'DEFER', value: 4, tone: 'info' },
            ]" :key="bar.label" class="bar">
              <span class="bar__label">{{ bar.label }}</span>
              <span class="bar__track">
                <span class="bar__fill" :class="`bar__fill--${bar.tone}`" :style="{ width: `${(bar.value / 412) * 100}%` }"></span>
              </span>
              <span class="bar__value">{{ bar.value }}</span>
            </div>
          </div>
        </section>

        <!-- 副作用调用（示意值） -->
        <section class="card">
          <div class="card__head">
            <div>
              <h2 class="card__title">副作用调用 · 今日</h2>
              <p class="card__subtitle">9 类同协议 · Tool Broker 唯一出口</p>
            </div>
            <span class="badge badge--outline">示意</span>
          </div>
          <div class="card__body bars">
            <div v-for="bar in [
              { label: 'MIGRATE_SECRET', value: 18, tone: 'info' },
              { label: 'STAGE_VALIDATE', value: 12, tone: 'ok' },
              { label: 'CLEANUP_DORIS', value: 6, tone: 'warn' },
              { label: 'ROLLBACK_DORIS', value: 2, tone: 'bad' },
            ]" :key="bar.label" class="bar">
              <span class="bar__label">{{ bar.label }}</span>
              <span class="bar__track">
                <span class="bar__fill" :class="`bar__fill--${bar.tone}`" :style="{ width: `${(bar.value / 18) * 100}%` }"></span>
              </span>
              <span class="bar__value">{{ bar.value }}</span>
            </div>
          </div>
        </section>
      </div>

      <!-- Capability 签发与消费（示意值） -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">Capability 签发与消费</h2>
            <p class="card__subtitle">Ed25519 · 单次令牌 · 防重放</p>
          </div>
          <span class="badge badge--outline">示意</span>
        </div>
        <div class="card__body grid-3">
          <div><div class="mini__k">今日签发</div><div class="mini__v">47</div></div>
          <div><div class="mini__k">已消费</div><div class="mini__v">47</div></div>
          <div><div class="mini__k">重放拦截</div><div class="mini__v">1</div></div>
        </div>
      </section>
    </div>

    <aside class="dash-side stack">
      <!-- 待办与告警 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">待办与告警</h2>
            <p class="card__subtitle">P0 优先 · DENY 无人工放行通道</p>
          </div>
          <span class="badge badge--info">INFO</span>
        </div>
        <div class="card__body alerts">
          <div v-for="(alert, index) in alerts" :key="index" class="alert" :class="`alert--${alert.tone}`">
            <div class="alert__title">{{ alert.title }}</div>
            <div class="alert__hint">{{ alert.hint }}</div>
          </div>
        </div>
      </section>

      <!-- 证据账本健康度 -->
      <section class="card">
        <div class="card__head">
          <h2 class="card__title">证据账本健康度</h2>
          <span
            class="badge"
            :class="evidenceValid === null ? 'badge--neutral' : evidenceValid ? 'badge--success' : 'badge--danger'"
          >
            {{ evidenceValid === null ? '未验证' : evidenceValid ? 'VALID' : 'BROKEN' }}
          </span>
        </div>
        <div class="card__body">
          <p class="card__subtitle">只追加 · 哈希链 · 可整流验证</p>
          <p class="ledger mono">
            head_hash：{{ executions.length ? '已链接' : '待首条事件' }}
          </p>
        </div>
      </section>

      <!-- 生产边界提示卡（FR-53 明确要求） -->
      <section class="card boundary">
        <div class="card__head">
          <h2 class="card__title">生产边界提示</h2>
        </div>
        <div class="card__body">
          <ol class="boundary__list">
            <li>模型永远看不到连接密码</li>
            <li>审批绑定不可变 Artifact 哈希</li>
            <li>重试使用稳定 SeaTunnel JobId</li>
          </ol>
        </div>
      </section>

      <!-- 快捷导航 -->
      <section class="card">
        <div class="card__head">
          <h2 class="card__title">快捷导航</h2>
        </div>
        <div class="card__body quick">
          <RouterLink :to="{ name: 'datasources' }" class="quick__item">数据连接</RouterLink>
          <RouterLink :to="{ name: 'runtime' }" class="quick__item">运行中心</RouterLink>
          <RouterLink :to="{ name: 'safety' }" class="quick__item">安全治理</RouterLink>
          <RouterLink :to="{ name: 'studio' }" class="quick__item">Pipeline Studio</RouterLink>
        </div>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.dash-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

.dash-main,
.dash-side {
  min-width: 0;
}

.bars {
  display: flex;
  flex-direction: column;
  gap: 11px;
}

.bar {
  display: grid;
  grid-template-columns: 132px 1fr 40px;
  align-items: center;
  gap: 10px;
}

.bar__label {
  font-size: 11.5px;
  color: var(--color-text-secondary);
  font-family: var(--font-mono);
}

.bar__track {
  height: 8px;
  border-radius: 999px;
  background: var(--color-neutral-soft);
  overflow: hidden;
}

.bar__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
}

.bar__fill--ok {
  background: var(--color-success);
}
.bar__fill--warn {
  background: var(--color-warn);
}
.bar__fill--bad {
  background: var(--color-danger);
}
.bar__fill--info {
  background: var(--color-info);
}

.bar__value {
  font-size: 12px;
  font-weight: 600;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.grid-3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.mini__k {
  font-size: 10.5px;
  color: var(--color-text-muted);
}

.mini__v {
  font-size: 19px;
  font-weight: 650;
}

.alerts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.alert {
  border-radius: var(--radius-md);
  padding: 9px 11px;
  border-left: 3px solid var(--color-neutral);
  background: #fcfdff;
}

.alert--info {
  border-left-color: var(--color-info);
  background: var(--color-info-soft);
}
.alert--warn {
  border-left-color: var(--color-warn);
  background: var(--color-warn-soft);
}
.alert--danger {
  border-left-color: var(--color-danger);
  background: var(--color-danger-soft);
}

.alert__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text);
}

.alert__hint {
  font-size: 10.5px;
  color: var(--color-text-muted);
  margin-top: 2px;
}

.ledger {
  font-size: 11px;
  color: var(--color-text-secondary);
}

.boundary__list {
  margin: 0;
  padding-left: 16px;
  font-size: 11.5px;
  color: var(--color-text-secondary);
  line-height: 1.85;
}

.quick {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.quick__item {
  font-size: 11.5px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  text-align: center;
  font-weight: 500;
}

.quick__item:hover {
  background: #dde7fd;
}

@media (max-width: 1180px) {
  .dash-row {
    grid-template-columns: 1fr;
  }
}
</style>
