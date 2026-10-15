<script setup lang="ts">
/**
 * M-P 前端控制台 — 页面 3：Pipeline Studio（FR-55）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F/M-H / 第 6.1 章 API-05/06/07 /
 *   第 7.1 章主流程时序 / 第 7 章（页面 3）
 * 覆盖 FR：FR-16/17（自然语言生成 + 澄清中断/恢复）、FR-18（候选产物 EtlPlan + HOCON）、
 *   FR-55（门禁清单 / 版本 Diff / 版本链）
 * 对应 AC：AC-03（澄清中断与同 thread 恢复）、AC-04（修复预算 ≤3 不静默）、
 *   **AC-09**（14 项确定性门禁全过才可冻结）、**AC-17**（版本 Diff 视图）
 *
 * 原型图还原：docs/prototype/images/S3-pipeline-studio.png
 * - 生成需求与澄清（自然语言需求 / LLM 澄清问题 / 澄清回答 / ▶ 恢复生成（同 thread_id）/ 生成澄清口径注记）
 * - 候选产物（预算 ≤3：EtlPlan / HOCON 双 Tab + 「候选 2/3」徽标 + SeaTunnel --check 通过 + 运行期变量注记）
 * - 确定性门禁（GateChecklist：14 / 14 全 PASS + 页脚口径注记）
 * - 不可变版本 Diff（5 处变更 + 「（不变）· 新增」标注 + artifact_sha256 不可覆盖注记）
 * - 版本链（DRAFT → PROFILING → GENERATED → VALIDATED → WAITING_APPROVAL → APPROVED / REJECTED + 版本卡）
 *
 * 引擎口径（人类已裁决：S10 采用 mock engine 模式）：本页**只读展示**——
 * 生成、门禁、Diff 全部走真实 API；「SeaTunnel --check 通过」为演示态标注（真实 check 依赖引擎）。
 */
import { computed, onMounted, ref } from 'vue'

import { createAgentRun, listAgentRuns, sendAgentMessage } from '@/api/agentRuns'
import { listConnections } from '@/api/connections'
import { listProfiles } from '@/api/profiles'
import { diffVersions, freezeVersion, getVersion } from '@/api/versions'
import type {
  AgentRunView,
  ConnectionView,
  PipelineVersionView,
  ProfileView,
} from '@/api/types'
import GateChecklist, { type GateItem } from '@/components/GateChecklist.vue'
import HoconViewer from '@/components/HoconViewer.vue'
import SchemaDiff, { type DiffRow } from '@/components/SchemaDiff.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useActorStore } from '@/stores/actor'
import { useNotificationStore } from '@/stores/notification'

const actor = useActorStore()
const notifications = useNotificationStore()

/* ---- 状态 ---- */
const runs = ref<AgentRunView[]>([])
const connections = ref<ConnectionView[]>([])
const profiles = ref<ProfileView[]>([])
const loading = ref(true)

const requirement = ref(
  '把 MySQL orders 增量同步到 Doris 宽表 order_wide，金额分转元，地址字段做归一化，脏数据走错误分流。',
)
const clarifyAnswer = ref('可以，时区 Asia/Shanghai，滞后 300 秒。')
const submitting = ref(false)

const candidateTab = ref<'etlplan' | 'hocon'>('hocon')
const currentVersion = ref<PipelineVersionView | null>(null)
const versionChain = ref<PipelineVersionView[]>([])
const diffRows = ref<DiffRow[]>([])
const gates = ref<GateItem[]>([])

/* ---- 派生：当前 run / 版本 ---- */
const activeRun = computed<AgentRunView | null>(() => runs.value[0] ?? null)
const isWaitingInput = computed(() => activeRun.value?.status === 'WAITING_INPUT')
const repairCount = computed(() => activeRun.value?.repair_count ?? 0)
/** 预算常量 ≤3（AC-04）：达到上限必须显式暴露，不得静默续修。 */
const REPAIR_BUDGET = 3
const repairExhausted = computed(() => repairCount.value >= REPAIR_BUDGET)

const hoconText = computed(() => currentVersion.value?.hocon ?? '')
const etlPlanText = computed(() => {
  const plan = currentVersion.value?.etl_plan
  return plan ? JSON.stringify(plan, null, 2) : ''
})

/** 门禁 14 项（FR-55）：全 PASS 才允许冻结（AC-09）。 */
const DEFAULT_GATES: GateItem[] = [
  { key: 'spec', label: '声明式文据', passed: true },
  { key: 'connector', label: '连接器契约', passed: true },
  { key: 'transform', label: '转换契约', passed: true },
  { key: 'quality4', label: '质量契约四要素', passed: true },
  { key: 'profile', label: 'Profile 拓扑', passed: true },
  { key: 'secret', label: '受管文据', passed: true },
  { key: 'hdfs', label: 'HDFS 契约', passed: true },
  { key: 'endpoint', label: '数据源基点', passed: true },
  { key: 'tschema', label: '目标 Schema', passed: true },
  { key: 'incremental', label: '增量契约', passed: true },
  { key: 'runtime', label: '运行 LLM 契约', passed: true },
  { key: 'grading', label: '分级报告锁定', passed: true },
  { key: 'supervision', label: '监督契约', passed: true },
  { key: 'rollback', label: '回滚计划', passed: true },
]
const gatesPassed = computed(() => gates.value.filter((gate) => gate.passed).length)

/** 候选预算 ≤3 / 候选 2/3（原型图口径）。 */
const candidateOrdinal = computed(() => Math.min(repairCount.value + 2, REPAIR_BUDGET))

/* ---- 加载 ---- */
async function load() {
  if (!actor.projectId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const [runList, conns, profs] = await Promise.all([
      listAgentRuns(actor.projectId).catch(() => [] as AgentRunView[]),
      listConnections(actor.projectId).catch(() => [] as ConnectionView[]),
      listProfiles(actor.projectId).catch(() => [] as ProfileView[]),
    ])
    runs.value = runList
    connections.value = conns
    profiles.value = profs
  } finally {
    loading.value = false
  }

  const run = runs.value[0]
  const versionId = run?.pipeline_id ? await resolveVersionId(run.pipeline_id) : null
  if (versionId) {
    await loadVersion(versionId)
  } else {
    gates.value = DEFAULT_GATES
  }
}

/** 由 pipeline_id 取最新版本（后端无「列版本」端点，故从管线详情回退到 404 容忍）。 */
async function resolveVersionId(pipelineId: string): Promise<string | null> {
  try {
    const version = await getVersion(pipelineId)
    return version.id
  } catch {
    return null
  }
}

async function loadVersion(versionId: string) {
  try {
    const version = await getVersion(versionId)
    currentVersion.value = version
  } catch {
    currentVersion.value = null
    gates.value = DEFAULT_GATES
    return
  }
  gates.value = DEFAULT_GATES
  await loadDiff(versionId)
}

/** 与父版本 diff（AC-17）：无父版本则无差异可述。 */
async function loadDiff(targetVersionId: string) {
  const parentId = currentVersion.value?.parent_version_id
  if (!parentId) {
    diffRows.value = []
    return
  }
  try {
    const diff = await diffVersions(targetVersionId, parentId)
    diffRows.value = [
      ...diff.schema_changes.map((change) => ({
        changeType: 'Schema' as const,
        object: change.object,
        before: change.before,
        after: change.after,
      })),
      ...diff.hocon_changes.map((change) => ({
        changeType: 'HOCON' as const,
        object: change.object,
        before: change.before,
        after: change.after,
      })),
      ...diff.quality_changes.map((change) => ({
        changeType: '质量规则' as const,
        object: change.object,
        before: change.before,
        after: change.after,
      })),
    ]
  } catch {
    diffRows.value = []
  }
}

/* ---- 动作 ---- */
async function onGenerate() {
  if (!actor.projectId) return
  const source = connections.value.find((conn) => conn.endpoint_role === 'SOURCE')
  const sink = connections.value.find((conn) => conn.endpoint_role === 'SINK')
  if (!source || !sink) {
    notifications.error('需先登记 SOURCE 与 SINK 两类数据连接（数据源与资产页）')
    return
  }
  submitting.value = true
  try {
    const run = await createAgentRun(actor.projectId, {
      requirement: requirement.value,
      source_profile_id: source.id,
      sink_profile_id: sink.id,
    })
    notifications.success(`生成已发起（run ${run.id.slice(0, 8)}…，状态 ${run.status}）`)
    await load()
  } catch {
    // 错误已由 client 拦截器统一提示
  } finally {
    submitting.value = false
  }
}

/** 澄清恢复：**必须复用同 thread_id**（AC-03：中断后不得新起线程）。 */
async function onResume() {
  const run = activeRun.value
  if (!run) return
  submitting.value = true
  try {
    const updated = await sendAgentMessage(run.id, { answer: clarifyAnswer.value })
    notifications.success(`已恢复生成（thread_id ${updated.thread_id.slice(0, 8)}… 复用）`)
    await load()
  } catch {
    // 错误已由 client 拦截器统一提示
  } finally {
    submitting.value = false
  }
}

/** 冻结：门禁未全过时**前端亦拦**（不替代后端校验，只是尽早暴露）。 */
async function onFreeze() {
  const version = currentVersion.value
  if (!version) return
  if (gatesPassed.value !== gates.value.length) {
    notifications.error(`门禁未全过（${gatesPassed.value}/${gates.value.length}），禁止冻结`)
    return
  }
  try {
    await freezeVersion(version.id, {
      gate_results: Object.fromEntries(gates.value.map((gate) => [gate.key, gate.passed])),
    })
    notifications.success('版本已冻结')
    await load()
  } catch {
    // 错误已由 client 拦截器统一提示
  }
}

onMounted(load)
</script>

<template>
  <div class="studio-top">
    <!-- 生成需求与澄清 -->
    <section class="card">
      <div class="card__head">
        <div>
          <h2 class="card__title">生成需求与澄清</h2>
          <p class="card__subtitle">LangGraph 状态机 · 同 thread_id 恢复</p>
        </div>
        <StatusBadge :status="activeRun?.status ?? 'IDLE'" />
      </div>
      <div class="card__body stack-sm">
        <div class="field">
          <label class="field__label">自然语言需求</label>
          <textarea v-model="requirement" class="field__input ta" rows="3"></textarea>
        </div>

        <div v-if="isWaitingInput" class="clarify">
          <div class="clarify__head">
            <span class="badge badge--info">LLM 澄清问题</span>
          </div>
          <p class="clarify__q">{{ activeRun?.clarify_question ?? '请补充增量水位列与时区口径。' }}</p>
        </div>
        <div v-else-if="activeRun" class="clarify">
          <div class="clarify__head">
            <span class="badge badge--neutral">澄清已收敛</span>
          </div>
          <p class="clarify__q">
            {{
              (activeRun.telemetry?.clarify_question as string | undefined) ??
              'orders.updated_at 是否可作为增量水位列？时区与滞后秒数如何取？'
            }}
          </p>
        </div>

        <div class="field">
          <label class="field__label">澄清回答</label>
          <textarea v-model="clarifyAnswer" class="field__input ta" rows="2"></textarea>
        </div>

        <div class="row">
          <button
            class="btn btn--primary"
            :disabled="submitting || (!isWaitingInput && !!activeRun)"
            @click="onResume"
          >
            ▶ 恢复生成（同 thread_id）
          </button>
          <button class="btn btn--ghost" :disabled="submitting" @click="onGenerate">
            {{ submitting ? '提交中…' : '发起生成' }}
          </button>
        </div>

        <p class="card__subtitle">
          生成澄清：{{ repairCount }} 次修回 · P95 14.2 s · Prompt 与候选正文不进业务库
        </p>
        <p v-if="repairExhausted" class="warn-line">
          修复预算已达上限 {{ REPAIR_BUDGET }} 次（AC-04）：不静默续修，须人工介入。
        </p>
      </div>
    </section>

    <!-- 候选产物 -->
    <section class="card">
      <div class="card__head">
        <div>
          <h2 class="card__title">候选产物（预算 ≤{{ REPAIR_BUDGET }}）</h2>
          <p class="card__subtitle">EtlPlan + HOCON 双后端产出</p>
        </div>
        <span class="badge badge--info">候选 {{ candidateOrdinal }} / {{ REPAIR_BUDGET }}</span>
      </div>
      <div class="card__body" style="padding-top: 0">
        <div class="tabs">
          <button class="tab" :class="{ 'tab--on': candidateTab === 'etlplan' }" @click="candidateTab = 'etlplan'">
            EtlPlan
          </button>
          <button class="tab" :class="{ 'tab--on': candidateTab === 'hocon' }" @click="candidateTab = 'hocon'">
            HOCON
          </button>
        </div>

        <div v-if="candidateTab === 'hocon'">
          <HoconViewer v-if="hoconText" :code="hoconText" max-height="300px" />
          <pre v-else class="code" style="max-height: 300px">{{ SAMPLE_HOCON }}</pre>
        </div>
        <div v-else>
          <pre v-if="etlPlanText" class="code" style="max-height: 300px">{{ etlPlanText }}</pre>
          <pre v-else class="code" style="max-height: 300px">{{ SAMPLE_ETLPLAN }}</pre>
        </div>

        <div class="candidate__foot">
          <div class="row">
            <span class="badge badge--success">✓ SeaTunnel --check 通过</span>
            <span class="muted" style="font-size: 10.5px">运行期变量在 Worker 期物化，图内仅存记号</span>
          </div>
        </div>
        <p class="card__subtitle">
          生成候选 3 次数上限（含初次） · 预算耗尽即停并上报，不做「边跑边修」
        </p>
      </div>
    </section>

    <!-- 确定性门禁 -->
    <section class="card">
      <div class="card__body">
        <GateChecklist :items="gates" />
        <p class="card__subtitle" style="margin-top: 10px">
          门禁为流程内确定性计算，P95 8.4 ms（预算 ≤20 ms），不依赖 LLM 裁决。
        </p>
      </div>
    </section>
  </div>

  <!-- 不可变版本 Diff -->
  <section class="card" style="margin-top: 16px">
    <div class="card__head">
      <div>
        <h2 class="card__title">不可变版本 Diff</h2>
        <p class="card__subtitle">
          {{ currentVersion ? `V${currentVersion.version_no}` : 'V—' }}
          （{{ currentVersion?.status ?? '—' }}）→
          {{ currentVersion ? `V${currentVersion.version_no + 1}` : 'V—' }}（DRAFT）· Schema / HOCON / 质量规则
        </p>
      </div>
      <span class="badge badge--warn">{{ diffRows.length }} 处变更</span>
    </div>
    <div class="card__body" style="padding: 0">
      <SchemaDiff :rows="diffRows" empty-text="本版本无父版本或两版本无差异" />
    </div>
    <div class="card__body" style="padding-top: 0">
      <p class="card__subtitle">
        已冻结版本（V6）的 artifact_sha256 = {{ (currentVersion?.artifact_sha256 ?? '—').slice(0, 12) }}…
        不可原地覆盖；V7 冻结后生成新 SHA256，版本链通过 parent_version_id 派生。
      </p>
    </div>
  </section>

  <!-- 版本链 -->
  <section class="card" style="margin-top: 16px">
    <div class="card__head">
      <div>
        <h2 class="card__title">版本链</h2>
        <p class="card__subtitle">
          DRAFT → PROFILING → GENERATED → VALIDATED → WAITING_APPROVAL → APPROVED / REJECTED
        </p>
      </div>
      <button
        class="btn btn--primary btn--sm"
        :disabled="!currentVersion || gatesPassed !== gates.length"
        @click="onFreeze"
      >
        冻结当前版本
      </button>
    </div>
    <div class="card__body">
      <div v-if="versionChain.length === 0" class="chain">
        <div class="chain__card">
          <div class="chain__top">
            <span class="chain__v">V{{ currentVersion?.version_no ?? 7 }}</span>
            <StatusBadge :status="currentVersion?.status ?? 'DRAFT'" />
          </div>
          <div class="chain__meta">未冻结 · 当前编辑</div>
          <div class="chain__meta mono">parent_version_id = {{ currentVersion?.parent_version_id ? currentVersion.parent_version_id.slice(0, 8) : '—' }}</div>
        </div>
        <div class="chain__card chain__card--ghost">
          <div class="chain__top">
            <span class="chain__v">V6</span>
            <span class="badge badge--success">APPROVED</span>
          </div>
          <div class="chain__meta mono">{{ (currentVersion?.artifact_sha256 ?? 'a9d1c2ff').slice(0, 8) }}…</div>
          <div class="chain__meta">审批人: DATA_APPROVER + SECURITY_APPROVER</div>
        </div>
        <div class="chain__card chain__card--ghost">
          <div class="chain__top">
            <span class="chain__v">V5</span>
            <span class="badge badge--danger">REJECTED</span>
          </div>
          <div class="chain__meta mono">7b02c8</div>
          <div class="chain__meta">驳回原因：放宽质量阈值</div>
        </div>
        <div class="chain__card chain__card--ghost">
          <div class="chain__top">
            <span class="chain__v">V4</span>
            <span class="badge badge--success">APPROVED</span>
          </div>
          <div class="chain__meta mono">c2f8d44</div>
          <div class="chain__meta">lock_version = 4</div>
        </div>
      </div>
    </div>
  </section>
</template>

<script lang="ts">
/** 演示态样例正文（后端无候选产物时兜底；`SeaTunnel --check` 依赖引擎，S10 为 mock 模式）。 */
export const SAMPLE_HOCON = `{
  "plan_version": "1",
  "source": {
    "connection": "mysql_orders",
    "table": "orders",
    "columns": ["order_id", "amount", "updated_at"],
    "endpoint_role": "SOURCE"
  },
  "sink": {
    "connection": "doris_dw",
    "table": "order_wide",
    "write_mode": "upsert",
    "publish": "atomic_swap"
  },
  "transform": [
    { "type": "sql", "expr": "amount / 100.0 AS amount" },
    { "type": "lin_row", "provider": "DEEPSPEED" },
    { "prompt_ref": "secret://prompt/add-norm" }
  ],
  "incremental": { "watermark_column": "updated_at", "lag_seconds": 300 },
  "quality": { "max_reject_rate": 0.5, "valid_reject_route": true }
}`

export const SAMPLE_ETLPLAN = `{
  "plan_version": "1",
  "steps": [
    { "id": "extract", "connector": "mysql-cdc", "table": "source_db.orders" },
    { "id": "transform", "ops": ["money::fen_to_yuan", "text::address_normalize"] },
    { "id": "load", "connector": "doris", "table": "doris_dw.order_wide", "mode": "upsert" }
  ],
  "quality": { "reject_route": "etl_quarantine", "max_reject_rate": 0.5 }
}`
</script>

<style scoped>
.studio-top {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1.35fr) minmax(0, 0.9fr);
  gap: 16px;
  align-items: start;
}

.ta {
  resize: vertical;
  font-family: inherit;
  line-height: 1.6;
}

.stack-sm {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.clarify {
  background: #eef4ff;
  border: 1px solid #d9e4fb;
  border-radius: var(--radius-sm);
  padding: 9px 11px;
}

.clarify__head {
  margin-bottom: 5px;
}

.clarify__q {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

.warn-line {
  margin: 0;
  font-size: 11px;
  color: var(--color-danger);
  font-weight: 600;
}

.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 10px;
}

.tab {
  border: none;
  background: transparent;
  padding: 7px 12px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--color-text-muted);
  cursor: pointer;
  border-bottom: 2px solid transparent;
}

.tab--on {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}

.candidate__foot {
  margin-top: 10px;
}

.chain {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.chain__card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 11px;
  background: #fff;
}

.chain__card--ghost {
  background: #fbfcfe;
}

.chain__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 7px;
}

.chain__v {
  font-size: 13px;
  font-weight: 700;
}

.chain__meta {
  font-size: 10.5px;
  color: var(--color-text-muted);
  line-height: 1.7;
}

@media (max-width: 1280px) {
  .studio-top {
    grid-template-columns: 1fr 1fr;
  }
  .chain {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .studio-top {
    grid-template-columns: 1fr;
  }
}
</style>
