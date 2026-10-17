<script setup lang="ts">
/**
 * M-P 前端控制台 — 不可变版本 Diff 独立视图（AC-17）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H（版本与不可变）/
 *   第 6.1 章 API-07 / 第 7 章（页面 3 的 Diff 区块）
 * 覆盖 FR：FR-27（版本 Diff：Schema / HOCON / 质量规则差异）
 * 对应 AC：**AC-17**（版本 Diff 视图）
 *
 * 口径：版本**只可派生、不可原地覆盖**——已冻结版本的 artifact_sha256 是不变量，
 * 任何修改都表现为「新版本 + parent_version_id」。故本页 diff 的 base/target
 * 顺序即语义（base → target 的变更），不允许前端隐式交换。
 */
import { computed, onMounted, ref } from 'vue'

import { diffVersions, getVersion } from '@/api/versions'
import type { PipelineVersionView, VersionDiffView } from '@/api/types'
import SchemaDiff, { type DiffRow } from '@/components/SchemaDiff.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { diffCounts, toDiffRows } from '@/utils/versionDiffRows'
import { useActorStore } from '@/stores/actor'

const actor = useActorStore()

const targetId = ref('')
const baseId = ref('')
const diff = ref<VersionDiffView | null>(null)
const targetVersion = ref<PipelineVersionView | null>(null)
const baseVersion = ref<PipelineVersionView | null>(null)
const loading = ref(false)
const errorText = ref('')

/** 三类正文级差异 → 表格行（映射逻辑见 `@/utils/versionDiffRows`，与 Studio 共用）。 */
const rows = computed<DiffRow[]>(() => toDiffRows(diff.value))

const counts = computed(() => diffCounts(diff.value))

/** 制品摘要级差异（`artifact_digests`，**恒可用**，不依赖 Checkpoint 正文）。 */
const artifactRows = computed(() =>
  Object.entries(diff.value?.artifact_digests ?? {}).map(([type, item]) => ({
    type,
    before: item.before,
    after: item.after,
    changed: item.changed,
  })),
)

async function submit() {
  if (!targetId.value || !baseId.value) {
    errorText.value = '请同时填写 target 与 base 两个版本 ID'
    return
  }
  if (targetId.value === baseId.value) {
    errorText.value = '同一版本自身对比无意义（版本不可原地覆盖）'
    return
  }
  loading.value = true
  errorText.value = ''
  diff.value = null
  try {
    const [result, target, base] = await Promise.all([
      diffVersions(targetId.value, baseId.value),
      getVersion(targetId.value).catch(() => null),
      getVersion(baseId.value).catch(() => null),
    ])
    diff.value = result
    targetVersion.value = target
    baseVersion.value = base
  } catch {
    errorText.value = 'Diff 失败：请确认两个版本属于同一 pipeline 且 ID 正确'
  } finally {
    loading.value = false
  }
}

/** 从 Studio 跳转时可用 query 参数预填（`?target=…&base=…`）。 */
onMounted(() => {
  void actor
  const params = new URLSearchParams(window.location.search)
  targetId.value = params.get('target') ?? ''
  baseId.value = params.get('base') ?? ''
  if (targetId.value && baseId.value) {
    void submit()
  }
})
</script>

<template>
  <section class="card">
    <div class="card__head">
      <div>
        <h2 class="card__title">版本对比</h2>
        <p class="card__subtitle">
          方向即语义：表达「base → target」的变更，反向对比请显式交换两侧
        </p>
      </div>
    </div>
    <div class="card__body">
      <div class="diff-form">
        <div class="field">
          <label class="field__label">Base 版本 ID（变更前）</label>
          <input v-model="baseId" class="field__input mono" placeholder="父版本 UUID" />
        </div>
        <div class="field">
          <label class="field__label">Target 版本 ID（变更后）</label>
          <input v-model="targetId" class="field__input mono" placeholder="目标版本 UUID" />
        </div>
        <button class="btn btn--primary" :disabled="loading" @click="submit">
          {{ loading ? '对比中…' : '对比' }}
        </button>
      </div>
      <p v-if="errorText" class="err">{{ errorText }}</p>
    </div>
  </section>

  <section v-if="diff" class="card" style="margin-top: 16px">
    <div class="card__head">
      <div>
        <h2 class="card__title">不可变版本 Diff</h2>
        <p class="card__subtitle">
          {{ baseVersion ? `V${baseVersion.version_number}` : baseId.slice(0, 8) }}
          （{{ baseVersion?.status ?? '—' }}）→
          {{ targetVersion ? `V${targetVersion.version_number}` : targetId.slice(0, 8) }}
          （{{ targetVersion?.status ?? '—' }}）· Schema / HOCON / 质量规则
        </p>
      </div>
      <div class="row">
        <span class="badge badge--info">Schema {{ counts.schema }}</span>
        <span class="badge badge--warn">HOCON {{ counts.hocon }}</span>
        <span class="badge badge--success">质量规则 {{ counts.quality }}</span>
      </div>
    </div>
    <div class="card__body" style="padding: 0">
      <p v-if="!diff.content_available" class="card__subtitle" style="padding: 12px 14px; margin: 0">
        两版本候选正文未能同时从 Checkpoint 取到（<span class="mono">content_available=false</span>），
        故 Schema / HOCON / 质量规则三个**正文级** diff 为空；下方另给出**制品摘要级**差异。
      </p>
      <SchemaDiff :rows="rows" empty-text="两版本在三类正文维度上均无差异（或正文不可得）" />
    </div>
    <div v-if="artifactRows.length" class="card__body" style="padding: 0">
      <table class="table">
        <thead>
          <tr>
            <th>制品摘要</th>
            <th>Before</th>
            <th>After</th>
            <th style="width: 110px">结论</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in artifactRows" :key="item.type">
            <td class="mono">{{ item.type }}</td>
            <td class="mono muted">{{ item.before.slice(0, 12) }}…</td>
            <td class="mono muted">{{ item.after.slice(0, 12) }}…</td>
            <td>
              <span class="badge" :class="item.changed ? 'badge--warn' : 'badge--neutral'">
                {{ item.changed ? 'CHANGED' : '不变' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="card__body" style="padding-top: 0">
      <div class="hash-grid">
        <div>
          <div class="hash-grid__k">Base artifact_sha256</div>
          <div class="hash-grid__v mono">{{ baseVersion?.artifact_sha256 ?? '—' }}</div>
        </div>
        <div>
          <div class="hash-grid__k">Target artifact_sha256</div>
          <div class="hash-grid__v mono">{{ targetVersion?.artifact_sha256 ?? '—' }}</div>
        </div>
        <div>
          <div class="hash-grid__k">Target parent_version_id</div>
          <div class="hash-grid__v mono">{{ targetVersion?.parent_version_id ?? '—' }}</div>
        </div>
        <div>
          <div class="hash-grid__k">Target 状态</div>
          <div class="hash-grid__v"><StatusBadge :status="targetVersion?.status ?? '—'" /></div>
        </div>
      </div>
      <p class="card__subtitle" style="margin-top: 12px">
        已冻结版本的 artifact_sha256 不可原地覆盖；派生新版本会生成新的 SHA256 并以
        parent_version_id 挂到版本链上——这正是「不可变」在接口层的体现。
      </p>
    </div>
  </section>

  <section v-else-if="!loading" class="card" style="margin-top: 16px">
    <div class="card__body">
      <div class="empty">
        填入两个版本 ID 后开始对比。<br />
        也可从 Pipeline Studio 的版本链进入（自动预填当前版本与其父版本）。
      </div>
    </div>
  </section>
</template>

<style scoped>
.diff-form {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 12px;
  align-items: end;
}

.err {
  margin: 10px 0 0;
  font-size: 11.5px;
  color: var(--color-danger);
}

.hash-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 20px;
  padding-top: 12px;
  border-top: 1px dashed var(--color-border);
}

.hash-grid__k {
  font-size: 10.5px;
  color: var(--color-text-muted);
  margin-bottom: 3px;
}

.hash-grid__v {
  font-size: 11px;
  word-break: break-all;
}

.empty {
  font-size: 11.5px;
  color: var(--color-text-muted);
  text-align: center;
  padding: 30px 0;
  line-height: 1.9;
}

@media (max-width: 900px) {
  .diff-form {
    grid-template-columns: 1fr;
  }
  .hash-grid {
    grid-template-columns: 1fr;
  }
}
</style>
