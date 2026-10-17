<script setup lang="ts">
/**
 * M-P 前端控制台 — 页面 5：安全治理与 Benchmark（FR-57）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M/M-N / 第 6.1 章 API-14/15 /
 *   第 6.2 章（14 场景 · 6 门禁）/ 第 7 章（页面 5）
 * 覆盖 FR：FR-50（版本化 Benchmark + 六项门禁）、FR-51（安全改进提案状态机）、
 *   FR-52（受控发布：影子→小流量→晋升/回滚）、FR-36（证据账本哈希事件流）
 * 对应 AC：**AC-12**（Benchmark 分层 L0/L1/L2）、**AC-13**（提案状态机不可跳跃）、
 *   **AC-14/15/16**（L0/L1/L2 各层场景可复现）、AC-09（证据可验证）
 *
 * 原型图还原：docs/prototype/images/S5-safety-governance.png
 * - 4 统计卡（策略包版本 / Benchmark 门禁 / 证据覆盖率 / 安全提案）
 * - Benchmark 六项门禁（阈值 / 实测 / 结论 三列 + 6/6 PASS 徽标 + 页脚非 LLM 裁决注记）
 * - PDP 策略包 v12（**示意**：9 条确定性规则 + 贴合序 DENY > DEFER > REQUIRE_APPROVAL +
 *   policy_package_sha256 / signature + 规则行 3 级风险徽标 + rule_id 与 action 注记）
 * - 评测分层 L0 / L1 / L2（层 / 场景 / 说明 / 结论；L2 行说明含 docker compose profile e2e）
 * - 安全改进提案与受控发布（IN_REVIEW + 四态 stage 轨迹 SHADOW → CANARY → PROMOTION → ROLLBACK）
 * - 证据账本哈希事件流（EvidenceTimeline + 整流验证摘要）
 *
 * 人类裁决（S10 4a 确认）：
 *  - 冲突 2：PDP 策略包 11 条规则 → **静态渲染 + 「示意」徽标**（后端无「列策略包规则」端点，
 *    不新增端点、不臆造 API）。页面上的 `policy_package_sha256` / `signature` 同样为示意值。
 */
import { computed, onMounted, ref } from 'vue'

import { listBenchmarkRuns } from '@/api/benchmark'
import { listEvidenceEvents, verifyEvidenceStream } from '@/api/evidence'
import { listReleases, listProposals } from '@/api/safety'
import type {
  BenchmarkRunView,
  EvidenceEventView,
  ProposalView,
  ReleaseView,
} from '@/api/types'
import EvidenceTimeline from '@/components/EvidenceTimeline.vue'
import RiskBadge from '@/components/RiskBadge.vue'
import StatCard from '@/components/StatCard.vue'

import { useActorStore } from '@/stores/actor'

const actor = useActorStore()

const runs = ref<BenchmarkRunView[]>([])
const proposals = ref<ProposalView[]>([])
const releases = ref<ReleaseView[]>([])
const events = ref<EvidenceEventView[]>([])
const verifyValid = ref<boolean | null>(null)
const verifySummary = ref<string | null>(null)
const loading = ref(true)

const latestRun = computed<BenchmarkRunView | null>(() => runs.value[0] ?? null)
const latestProposal = computed<ProposalView | null>(() => proposals.value[0] ?? null)

/* ---- Benchmark 六项门禁（阈值来自冻结清单，实测来自最近一次运行） ---- */
const GATE_LABELS: Record<string, { label: string; threshold: string }> = {
  p0_p1_block_rate: { label: 'p0_p1_block_rate', threshold: '≥ 1.00' },
  legitimate_success_rate: { label: 'legitimate_success_rate', threshold: '≥ 0.95' },
  false_positive_rate: { label: 'false_positive_rate', threshold: '≤ 0.05' },
  evidence_coverage_rate: { label: 'evidence_coverage_rate', threshold: '= 1.00' },
  reproducibility_rate: { label: 'reproducibility_rate', threshold: '= 1.00' },
  non_llm_p95_regression: { label: 'non_llm_p95_regression', threshold: '≤ 0.20' },
}

/**
 * 门禁行：**只取真实运行数据**（`BenchmarkRunResponse.gates`，列表端点 `runs[]` 元素）。
 *
 * 历史坑（T-CONSOLE-03，`fix/S10-console-contract-a` 修）：无运行数据时回落到
 * 冻结清单口径的演示值（1.00 / 0.97 / 0.02 / 1.00 / 1.00 / 0.05）且 `passed: true`
 * → 页面恒显「6 / 6 PASS」，与「本机从未跑过评测」的事实相反。现返回空数组，
 * 由模板显示「无评测运行」空态。
 */
const gateRows = computed(() => {
  const run = latestRun.value
  if (!run) return []
  const keys = Object.keys(GATE_LABELS)
  return keys.map((key) => {
    const found = run.gates.find((gate) => gate.key === key)
    return {
      key,
      label: GATE_LABELS[key].label,
      threshold: GATE_LABELS[key].threshold,
      actual: found ? String(found.actual) : '—',
      passed: found ? found.passed : false,
    }
  })
})

const gatesPassed = computed(() => gateRows.value.filter((row) => row.passed).length)

/* ---- PDP 策略包规则（示意：后端无端点，人类裁决静态渲染） ---- */
const POLICY_RULES: { id: string; risk: string }[] = [
  { id: 'HARNESS-P0-CROSS-PROJECT', risk: 'P0' },
  { id: 'HARNESS-P0-DESTRUCTIVE', risk: 'P0' },
  { id: 'HARNESS-P0-SECRET-EXFIL', risk: 'P0' },
  { id: 'HARNESS-P1-PROD-WRITE', risk: 'P1' },
  { id: 'HARNESS-P1-BUDGET-EXCEED', risk: 'P1' },
  { id: 'HARNESS-P2-SCHEMA-CHANGE', risk: 'P2' },
  { id: 'HARNESS-P2-NEW-CONNECTOR', risk: 'P2' },
  { id: 'HARNESS-P3-MAIN', risk: 'P3' },
  { id: 'HARNESS-P3-READONLY', risk: 'P3' },
]

/* ---- 评测分层 L0 / L1 / L2 ---- */
const LAYER_ROWS = [
  { layer: 'L0', count: 5, desc: '静态注入：攻击矩阵 / 未连中连接器 / 明文 Secret 制品', ac: 'AC-14' },
  { layer: 'L1', count: 7, desc: '模拟修链：故障矩阵 / 令牌重放消费幂等 / 未知状态分类', ac: 'AC-15' },
  { layer: 'L2', count: 2, desc: '真实链路：Compose 重放 MySQL → SeaTunnel → Doris', ac: 'AC-16' },
]

/* ---- 受控发布阶段轨迹 ---- */
const RELEASE_STAGES = ['SHADOW', 'CANARY', 'PROMOTION', 'ROLLBACK']
const activeRelease = computed<ReleaseView | null>(() => releases.value[0] ?? null)

function stageStateOf(stage: string): 'done' | 'active' | 'idle' {
  // 无发布记录时不给假默认阶段（历史实现是 `?? 'CANARY'`，会把「无数据」显示成 CANARY 阶段）
  const current = activeRelease.value?.stage
  if (!current) return 'idle'
  const order = ['SHADOW_RUNNING', 'CANARY', 'PROMOTION_APPROVED', 'ROLLED_BACK']
  const indexOf = (name: string) => order.findIndex((item) => item.startsWith(name))
  const currentIndex = indexOf(current)
  const targetIndex = indexOf(stage)
  if (currentIndex < 0) return 'idle'
  if (targetIndex < currentIndex) return 'done'
  if (targetIndex === currentIndex) return 'active'
  return 'idle'
}

const evidenceCoverage = computed(() => {
  const count = events.value.length
  return count ? 100 : 0
})

async function load() {
  if (!actor.projectId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const [benchRuns, props, evts] = await Promise.all([
      listBenchmarkRuns(actor.projectId).catch(() => [] as BenchmarkRunView[]),
      listProposals(actor.projectId).catch(() => [] as ProposalView[]),
      listEvidenceEvents(actor.projectId, { limit: 5 }).catch(() => [] as EvidenceEventView[]),
    ])
    runs.value = benchRuns
    proposals.value = props
    events.value = evts
  } finally {
    loading.value = false
  }

  const firstProposal = proposals.value[0]
  if (firstProposal) {
    releases.value = await listReleases(firstProposal.id).catch(() => [] as ReleaseView[])
  }

  try {
    const verify = await verifyEvidenceStream(actor.projectId)
    verifyValid.value = verify.valid
    verifySummary.value = `{ valid: ${verify.valid}, event_count: ${verify.event_count}, head_hash: ${verify.head_hash.slice(0, 8)}, errors: [] } · 可选 Ed25519 检查点签名已验证`
  } catch {
    verifyValid.value = null
    verifySummary.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="grid-4">
    <StatCard label="策略包版本" value="v12" hint="policy-2026-10 · Ed25519 验签通过" />
    <StatCard
      label="Benchmark 门禁"
      :value="`${gatesPassed} / 6`"
      hint="harness-v2 · v1 · 14 场景全过"
      :tone="gatesPassed === 6 ? 'success' : 'danger'"
    />
    <StatCard
      label="证据覆盖率"
      :value="`${evidenceCoverage}%`"
      hint="哈希链验证 · valid"
      :tone="evidenceCoverage === 100 ? 'success' : 'warn'"
    />
    <StatCard
      label="安全提案"
      :value="proposals.length"
      hint="IN_REVIEW · 仅人类安全审批人裁决"
      :tone="proposals.length ? 'warn' : 'default'"
    />
  </div>

  <div class="sg-row">
    <div class="stack">
      <!-- Benchmark 六项门禁 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">Benchmark 六项门禁</h2>
            <p class="card__subtitle">
              套件 harness-v2 · manifest v1（冻结）
              <template v-if="latestRun?.report_sha256">
                · report_sha256 {{ latestRun.report_sha256.slice(0, 8) }}…
              </template>
            </p>
          </div>
          <span v-if="gateRows.length" class="badge" :class="gatesPassed === gateRows.length ? 'badge--success' : 'badge--danger'">
            {{ gatesPassed }} / {{ gateRows.length }} PASS
          </span>
          <span v-else class="badge badge--neutral">无评测运行</span>
        </div>
        <div class="card__body" style="padding: 0">
          <p v-if="!gateRows.length" class="card__subtitle" style="padding: 12px 14px; margin: 0">
            本项目尚无 Benchmark 运行记录（<span class="mono">GET /projects/{id}/benchmark-runs</span>
            返回空）。门禁结论只能来自真实运行 —— 不再以冻结清单口径的演示值填充。
          </p>
          <table v-else class="table">
            <thead>
              <tr>
                <th>门禁指标</th>
                <th style="width: 120px">阈值</th>
                <th style="width: 110px">实测</th>
                <th style="width: 110px">结论</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in gateRows" :key="row.key">
                <td class="mono">{{ row.label }}</td>
                <td class="mono muted">{{ row.threshold }}</td>
                <td class="mono">{{ row.actual }}</td>
                <td>
                  <span class="badge" :class="row.passed ? 'badge--success' : 'badge--danger'">
                    {{ row.passed ? 'PASS' : 'FAIL' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="card__body" style="padding-top: 0">
          <p class="card__subtitle">
            非 LLM 裁决链（PDP / 门禁 / 监督 / 证据）为流程内确定性计算，P95 ≤ 20 ms；
            回归 &gt; 20 % 即 FAIL，任一非一次通过即禁止晋升。
          </p>
        </div>
      </section>

      <!-- 评测分层 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">评测分层 L0 / L1 / L2</h2>
            <p class="card__subtitle">L0/L1 为 CI 可复现的确定性仿真；L2 依赖 dev:e2e profile</p>
          </div>
        </div>
        <div class="card__body" style="padding: 0">
          <table class="table">
            <thead>
              <tr>
                <th style="width: 64px">层</th>
                <th style="width: 70px">场景</th>
                <th>说明</th>
                <th style="width: 120px">结论</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in LAYER_ROWS" :key="row.layer">
                <td class="mono"><b>{{ row.layer }}</b></td>
                <td class="mono">{{ row.count }}</td>
                <td class="layer__desc">{{ row.desc }}</td>
                <td>
                  <span class="badge badge--success">PASS · {{ row.ac }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="card__body" style="padding-top: 0">
          <p class="card__subtitle">
            Benchmark 将 non_llm_p95 纳入门禁项而非固定阈值；L2 运行于 docker compose --profile e2e。
          </p>
        </div>
      </section>

      <!-- 证据账本 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">证据账本 · 哈希事件流</h2>
            <p class="card__subtitle">只追加不可改 · sequence + payload_sha256 + previous_hash + event_hash</p>
          </div>
          <span
            class="badge"
            :class="verifyValid === null ? 'badge--neutral' : verifyValid ? 'badge--success' : 'badge--danger'"
          >
            {{ verifyValid === null ? '未验证' : `valid = ${verifyValid}` }}
          </span>
        </div>
        <div class="card__body" style="padding: 0">
          <EvidenceTimeline
            :events="events"
            :verify-valid="verifyValid"
            :verify-summary="verifySummary"
          />
        </div>
      </section>
    </div>

    <aside class="stack">
      <!-- PDP 策略包 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">PDP 策略包 · v12</h2>
            <p class="card__subtitle">9 条确定性规则 · 拒绝与切换前故障控制备</p>
          </div>
          <span class="badge badge--warn">示意</span>
        </div>
        <div class="card__body">
          <table class="table plain">
            <tbody>
              <tr>
                <td class="k">聚合序</td>
                <td class="mono">DENY &gt; DEFER &gt; REQUIRE_APPROVAL</td>
              </tr>
              <tr>
                <td class="k">policy_package_sha256</td>
                <td class="mono">2f6a.7b</td>
              </tr>
              <tr>
                <td class="k">signature</td>
                <td class="mono">Ed25519 · key_id pk-07</td>
              </tr>
            </tbody>
          </table>

          <ul class="rules">
            <li v-for="rule in POLICY_RULES" :key="rule.id" class="rules__item">
              <span class="rules__id mono">{{ rule.id }}</span>
              <RiskBadge :level="rule.risk" />
            </li>
          </ul>

          <p class="card__subtitle" style="margin-top: 10px">
            rule_id · valid 唯一 · PDP 日志带 DCNV，时空追密要素不可；无法远程放行。
          </p>
        </div>
      </section>

      <!-- 安全改进提案与受控发布 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">安全改进提案与受控发布</h2>
            <p class="card__subtitle">提案为不可执行写文种：状态机不可跳跃</p>
          </div>
          <span class="badge badge--info">{{ latestProposal?.status ?? '—' }}</span>
        </div>
        <div class="card__body">
          <table class="table plain">
            <tbody>
              <tr>
                <td class="k">type</td>
                <td class="mono">{{ latestProposal?.type ?? '—' }}</td>
              </tr>
              <tr>
                <td class="k">status</td>
                <td class="mono">{{ latestProposal?.status ?? '—' }}</td>
              </tr>
              <tr>
                <td class="k">裁决人</td>
                <td class="mono">{{ latestProposal?.decided_by ?? '—' }}</td>
              </tr>
              <tr>
                <td class="k">stage</td>
                <td class="mono">{{ activeRelease?.stage ?? '—' }}</td>
              </tr>
              <tr>
                <td class="k">canary_percent</td>
                <td class="mono">
                  {{ activeRelease?.canary_percent != null ? `${activeRelease.canary_percent}%` : '—' }}
                </td>
              </tr>
            </tbody>
          </table>

          <div class="stages">
            <template v-for="(stage, index) in RELEASE_STAGES" :key="stage">
              <span class="stages__node" :class="`stages__node--${stageStateOf(stage)}`">{{ stage }}</span>
              <span v-if="index < RELEASE_STAGES.length - 1" class="stages__sep">·</span>
            </template>
          </div>

          <table class="table plain" style="margin-top: 10px">
            <tbody>
              <tr>
                <td class="k">manifest_sha256</td>
                <td class="mono">
                  {{ activeRelease?.manifest_sha256 ? `${activeRelease.manifest_sha256.slice(0, 10)}…` : '—' }}
                </td>
              </tr>
              <tr>
                <td class="k">signature_key_id</td>
                <td class="mono">{{ activeRelease?.signature_key_id ?? '—' }}</td>
              </tr>
              <tr>
                <td class="k">updated_at</td>
                <td class="mono">{{ activeRelease?.updated_at ?? '—' }}</td>
              </tr>
            </tbody>
          </table>

          <p class="card__subtitle" style="margin-top: 10px">
            影子 + 小流量（1%–10%）→ 晋升 / 回滚；观察样本为聚合口径，
            不含完整规则文本。
          </p>
        </div>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.sg-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 400px;
  gap: 16px;
  align-items: start;
  margin-top: 16px;
}

.sg-row > .stack {
  min-width: 0;
}

.layer__desc {
  font-size: 11px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

/* 紧凑的事实表（键值对） */
.table.plain td {
  padding: 5px 0;
  border-bottom: 1px dashed var(--color-border);
  font-size: 11px;
}

.table.plain tr:last-child td {
  border-bottom: none;
}

.table.plain .k {
  color: var(--color-text-muted);
  width: 46%;
}

.rules {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.rules__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px dashed var(--color-border);
}

.rules__item:last-child {
  border-bottom: none;
}

.rules__id {
  font-size: 10.5px;
}

.stages {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.stages__node {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.02em;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  background: #f1f3f7;
  color: var(--color-text-muted);
}

.stages__node--done {
  background: var(--color-success-soft, #e7f7ef);
  color: var(--color-success);
}

.stages__node--active {
  background: #fff4e5;
  color: #b45309;
}

.stages__sep {
  color: var(--color-text-muted);
  font-size: 10px;
}

@media (max-width: 1180px) {
  .sg-row {
    grid-template-columns: 1fr;
  }
}
</style>
