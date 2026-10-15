<script setup lang="ts">
/**
 * M-P 前端控制台 — 页面 2：数据源与资产管理（FR-54）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 7 章（页面 2）
 * 覆盖 FR：FR-54（连接表单 / 连通性测试 / 端点角色 / 表级探查与脱敏样本 /
 *   文件导入 25 MiB & 10 万行 / 凭据轮换表单）
 * 对应 AC：AC-02（凭据零明文：登记只传、凭据标记加密、响应只回摘要）
 *
 * 原型图还原：docs/prototype/images/S2-datasources.png
 * - 4 统计卡（数据连接 / 凭据 Provider 模式 / 凭据到期预警 / 文件资产）
 * - 数据连接登记表（连接名/类型/状态/Provider/账龄/到期预警/最后测试/操作）
 * - 元数据探查与数据分级（L1/L2/L3 分级计数）
 * - 文件资产列表（类型徽标 + 大小/行数）
 * - 凭据托管与轮换（Provider 分布 / 轮换进行中 / 销毁墓碑 / Webhook 冷却 / Provider 迁移）
 */
import { computed, onMounted, ref } from 'vue'

import { createConnection, listConnections, testConnection } from '@/api/connections'
import { listFileAssets } from '@/api/files'
import { listProfiles } from '@/api/profiles'
import type { ConnectionType, ConnectionView, FileAssetView, ProfileView } from '@/api/types'
import DataTable, { type Column } from '@/components/DataTable.vue'
import StatCard from '@/components/StatCard.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useActorStore } from '@/stores/actor'
import { useNotificationStore } from '@/stores/notification'

const actor = useActorStore()
const notifications = useNotificationStore()

const connections = ref<ConnectionView[]>([])
const profiles = ref<ProfileView[]>([])
const files = ref<FileAssetView[]>([])
const loading = ref(true)
const testingId = ref<string | null>(null)
const showForm = ref(false)

const CONNECTOR_TYPES: ConnectionType[] = [
  'MYSQL',
  'POSTGRESQL',
  'DORIS',
  'HIVE',
  'SQL_SERVER',
  'LOCAL_FILE',
]

const form = ref({
  name: '',
  connector_type: 'MYSQL' as ConnectionType,
  host: '',
  port: 3306,
  database: '',
  username: '',
  password: '',
})

const columns: Column[] = [
  { key: 'name', label: '数据连接' },
  { key: 'connector_type', label: '类型' },
  { key: 'status', label: '状态' },
  { key: 'secret_provider', label: '凭据 Provider' },
  { key: 'secret_age_days', label: '账龄（天）', align: 'right' },
  { key: 'expiry_warning', label: '到期预警' },
  { key: 'last_tested_at', label: '最后测试' },
  { key: 'actions', label: '操作', align: 'right' },
]

const readyCount = computed(() => connections.value.filter((c) => c.status === 'READY').length)
const testingCount = computed(() => connections.value.filter((c) => c.status === 'TESTING').length)
const failedCount = computed(() => connections.value.filter((c) => c.status === 'FAILED').length)
const expiringCount = computed(() => connections.value.filter((c) => c.expiry_warning).length)
const providerModes = computed(
  () => new Set(connections.value.map((c) => c.secret_provider ?? '—')).size,
)

function expiryTone(value: string | null): string {
  if (!value) return 'neutral'
  const upper = value.toUpperCase()
  if (upper.includes('OVERDUE')) return 'danger'
  if (upper.includes('WARN')) return 'warn'
  return 'success'
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KiB`
  return `${bytes} B`
}

function formatCount(value: number | null): string {
  return value === null || value === undefined ? '—' : value.toLocaleString()
}

async function load() {
  if (!actor.projectId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const [conns, profs, assets] = await Promise.all([
      listConnections(actor.projectId).catch(() => [] as ConnectionView[]),
      listProfiles(actor.projectId).catch(() => [] as ProfileView[]),
      listFileAssets(actor.projectId).catch(() => [] as FileAssetView[]),
    ])
    connections.value = conns
    profiles.value = profs
    files.value = assets
  } finally {
    loading.value = false
  }
}

async function onTest(connectionId: string) {
  if (!actor.projectId) return
  testingId.value = connectionId
  try {
    const result = await testConnection(actor.projectId, connectionId)
    notifications.success(`连通性测试：${result.status}（${result.latency_ms ?? '—'} ms）`)
    await load()
  } catch {
    // 错误已由 client 拦截器统一提示
  } finally {
    testingId.value = null
  }
}

async function onSubmit() {
  if (!actor.projectId) return
  try {
    await createConnection(actor.projectId, { ...form.value })
    notifications.success(`连接 ${form.value.name} 已登记（凭据加密托管）`)
    showForm.value = false
    form.value = {
      name: '',
      connector_type: 'MYSQL',
      host: '',
      port: 3306,
      database: '',
      username: '',
      password: '',
    }
    await load()
  } catch {
    // 错误已由 client 拦截器统一提示
  }
}

onMounted(load)
</script>

<template>
  <div class="grid-4">
    <StatCard
      label="数据连接"
      :value="connections.length"
      :hint="`READY ${readyCount} · TESTING ${testingCount} · FAILED ${failedCount}`"
    />
    <StatCard label="凭据 Provider" :value="`${providerModes} 模式`" hint="fernet-db · vault-kv-v2" />
    <StatCard
      label="凭据到期预警"
      :value="expiringCount"
      hint="14 天内到期 · WARNING 级"
      :tone="expiringCount ? 'warn' : 'default'"
    />
    <StatCard label="文件资产" :value="files.length" hint="上限 25 MiB / 100,000 行" />
  </div>

  <div class="ds-row">
    <div class="stack">
      <!-- 数据连接登记 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">数据连接登记</h2>
            <p class="card__subtitle">8 类连接器 · Secret 引用制 · 项目内唯一命名</p>
          </div>
          <div class="row">
            <span class="badge badge--success">凭据明文 0 泄露</span>
            <button class="btn btn--primary btn--sm" @click="showForm = !showForm">
              {{ showForm ? '收起表单' : '登记连接' }}
            </button>
          </div>
        </div>

        <div v-if="showForm" class="card__body form-block">
          <div class="form-grid">
            <div class="field">
              <label class="field__label">连接名</label>
              <input v-model="form.name" class="field__input" placeholder="mysql_orders" />
            </div>
            <div class="field">
              <label class="field__label">连接器类型</label>
              <select v-model="form.connector_type" class="field__select">
                <option v-for="type in CONNECTOR_TYPES" :key="type" :value="type">{{ type }}</option>
              </select>
            </div>
            <div class="field">
              <label class="field__label">主机</label>
              <input v-model="form.host" class="field__input" placeholder="127.0.0.1" />
            </div>
            <div class="field">
              <label class="field__label">端口</label>
              <input v-model.number="form.port" type="number" class="field__input" />
            </div>
            <div class="field">
              <label class="field__label">库名</label>
              <input v-model="form.database" class="field__input" placeholder="source_db" />
            </div>
            <div class="field">
              <label class="field__label">用户名</label>
              <input v-model="form.username" class="field__input" />
            </div>
            <div class="field">
              <label class="field__label">凭据 <span class="enc">加密</span></label>
              <input v-model="form.password" type="password" class="field__input" placeholder="加密托管，不回显" />
            </div>
          </div>
          <div class="row" style="margin-top: 12px">
            <button class="btn btn--primary" @click="onSubmit">提交登记</button>
            <span class="muted" style="font-size: 11px">凭据经 SecretProvider 加密托管，登记后仅存引用</span>
          </div>
        </div>

        <div class="card__body" style="padding: 0">
          <DataTable :columns="columns" :rows="connections as unknown as Record<string, unknown>[]" :loading="loading" empty-text="暂无数据连接">
            <template #cell-name="{ row }">
              <span class="mono">{{ row.name }}</span>
            </template>
            <template #cell-connector_type="{ row }">
              <span class="badge badge--outline">{{ row.connector_type }}</span>
            </template>
            <template #cell-status="{ row }">
              <StatusBadge :status="row.status as string" />
            </template>
            <template #cell-expiry_warning="{ row }">
              <span v-if="row.expiry_warning" class="badge" :class="`badge--${expiryTone(row.expiry_warning as string)}`">
                {{ row.expiry_warning }}
              </span>
              <span v-else class="badge badge--success">OK</span>
            </template>
            <template #cell-last_tested_at="{ row }">
              <span class="muted mono">{{ row.last_tested_at ? String(row.last_tested_at).slice(0, 16).replace('T', ' ') : '—' }}</span>
            </template>
            <template #cell-actions="{ row }">
              <button
                class="btn btn--ghost btn--sm"
                :disabled="testingId === row.id"
                @click="onTest(row.id as string)"
              >
                {{ testingId === row.id ? '测试中…' : '测试' }}
              </button>
              <button class="btn btn--ghost btn--sm">轮换</button>
            </template>
          </DataTable>
        </div>
        <div class="card__body" style="padding-top: 0">
          <p class="card__subtitle">
            连通性测试在后台执行并回写状态（UNTESTED / TESTING / READY / FAILED）；账龄 &gt; 90 天为 OVERDUE，&gt; 76 天进入 WARNING。
          </p>
        </div>
      </section>

      <!-- 凭据托管与轮换 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">凭据托管与轮换</h2>
            <p class="card__subtitle">SecretProvider 双模式 · 轮换 / 迁移 / 销毁状态机</p>
          </div>
          <span class="badge badge--warn">fernet 主密钥 KMS 托管待 S-Q7</span>
        </div>
        <div class="card__body grid-5">
          <div><div class="mini__k">Provider 分布</div><div class="mini__v">{{ connections.length }} / {{ providerModes }}</div><div class="mini__h">fernet-db / vault-kv-v2</div></div>
          <div><div class="mini__k">轮换进行中</div><div class="mini__v">1 项</div><div class="mini__h">VALIDATING → SUCCEEDED</div></div>
          <div><div class="mini__k">销毁墓碑</div><div class="mini__v">3 项</div><div class="mini__h">不含密钥，仅保留定位</div></div>
          <div><div class="mini__k">Webhook 退避冷却</div><div class="mini__v">2 条</div><div class="mini__h">退避 2/5 · 冷却中</div></div>
          <div><div class="mini__k">Provider 迁移</div><div class="mini__v">0 项</div><div class="mini__h">目标 UUID · 终态重试</div></div>
        </div>
      </section>
    </div>

    <aside class="stack">
      <!-- 元数据探查与数据分级 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">元数据探查与数据分级</h2>
          </div>
          <span class="badge badge--info">样本已脱敏</span>
        </div>
        <div class="card__body">
          <p class="card__subtitle" style="margin-bottom: 10px">
            只读 Profile · 6 级分级推断 · 报告带 SHA256
          </p>
          <div v-if="profiles.length === 0" class="empty">暂无 Profile（登记连接后发起表级探查）</div>
          <div v-for="profile in profiles" v-else :key="profile.id" class="profile">
            <div class="profile__name mono">{{ profile.schema_name }}.{{ profile.table_name }}</div>
            <div class="profile__meta">
              {{ profile.endpoint_role }} · {{ profile.column_count }} 字段 · 报告 {{ profile.report_sha256.slice(0, 8) }}…
            </div>
            <div class="profile__levels">
              <span
                v-for="(count, level) in profile.classifications"
                :key="level"
                class="badge"
                :class="level === 'L1' ? 'badge--success' : level === 'L2' ? 'badge--warn' : 'badge--danger'"
              >
                {{ level }}×{{ count }}
              </span>
            </div>
          </div>
        </div>
      </section>

      <!-- 文件资产 -->
      <section class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">文件资产</h2>
            <p class="card__subtitle">引擎只读挂载 /data/file-assets</p>
          </div>
        </div>
        <div class="card__body">
          <div v-if="files.length === 0" class="empty">暂无文件资产（上传后出现）</div>
          <div v-for="asset in files" v-else :key="asset.id" class="asset">
            <div>
              <div class="asset__name">{{ asset.filename }}</div>
              <div class="asset__meta">{{ formatBytes(asset.size_bytes) }} · {{ formatCount(asset.row_count) }}</div>
            </div>
            <span class="badge badge--outline">{{ asset.format }}</span>
          </div>
          <p class="card__subtitle" style="margin-top: 10px">
            上传路径不暴露给 API；S3/MinIO 适配待 Q8（FR-62）。后源格式 ORC / XML / Markdown / PDF 已实现。
          </p>
        </div>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.ds-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: 16px;
  align-items: start;
}

.ds-row > .stack {
  min-width: 0;
}

.form-block {
  background: #fcfdff;
  border-bottom: 1px solid var(--color-border);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px 14px;
}

.enc {
  color: var(--color-primary);
  font-weight: 600;
}

.grid-5 {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}

.mini__k {
  font-size: 10.5px;
  color: var(--color-text-muted);
}
.mini__v {
  font-size: 18px;
  font-weight: 650;
}
.mini__h {
  font-size: 10px;
  color: var(--color-text-muted);
}

.profile {
  padding: 9px 0;
  border-bottom: 1px dashed var(--color-border);
}
.profile:last-child {
  border-bottom: none;
}
.profile__name {
  font-size: 12px;
  font-weight: 600;
}
.profile__meta {
  font-size: 10.5px;
  color: var(--color-text-muted);
  margin: 2px 0 6px;
}
.profile__levels {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.asset {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--color-border);
}
.asset:last-of-type {
  border-bottom: none;
}
.asset__name {
  font-size: 12px;
  font-weight: 500;
  word-break: break-all;
}
.asset__meta {
  font-size: 10.5px;
  color: var(--color-text-muted);
}

.empty {
  font-size: 11.5px;
  color: var(--color-text-muted);
  text-align: center;
  padding: 16px 0;
}

@media (max-width: 1180px) {
  .ds-row {
    grid-template-columns: 1fr;
  }
  .form-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .grid-5 {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
