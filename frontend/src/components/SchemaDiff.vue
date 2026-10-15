<script setup lang="ts">
/**
 * M-P 前端控制台 — 版本 Diff 表（AC-17：Schema / HOCON / 质量规则差异）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H（版本 Diff 后端）/
 *   第 6.1 章 API-07 / 第 7 章（页面 3）
 * 覆盖 FR：FR-27（版本 Diff）、FR-55（Studio 内 Diff 视图）
 * 对应 AC：**AC-17**（版本 Diff 视图：Schema 新增/删除/类型变更 + HOCON + 质量规则）
 *
 * 还原口径（原型图）：变更类型徽标（Schema 蓝 / HOCON 橙 / 质量规则绿 / 增量蓝）+
 * 「变更前 → 变更后」两列；未变更项以「（不变）」标注（原型图 `.5（不变）· 新增 not_null`）。
 */
export interface DiffRow {
  changeType: 'Schema' | 'HOCON' | '质量规则' | '增量'
  object: string
  before: string | null
  after: string | null
  unchanged?: boolean
}

defineProps<{ rows: DiffRow[]; emptyText?: string }>()

const TYPE_CLASS: Record<string, string> = {
  Schema: 'badge--info',
  HOCON: 'badge--warn',
  质量规则: 'badge--success',
  增量: 'badge--info',
}
</script>

<template>
  <table class="table diff">
    <thead>
      <tr>
        <th style="width: 104px">变更类型</th>
        <th style="width: 30%">对象</th>
        <th>变更前</th>
        <th>变更后</th>
      </tr>
    </thead>
    <tbody>
      <tr v-if="rows.length === 0">
        <td colspan="4" class="diff__empty">{{ emptyText ?? '两个版本无差异' }}</td>
      </tr>
      <tr v-for="(row, index) in rows" v-else :key="index">
        <td>
          <span class="badge" :class="TYPE_CLASS[row.changeType] ?? 'badge--neutral'">
            {{ row.changeType }}
          </span>
        </td>
        <td class="table__mono">{{ row.object }}</td>
        <td class="table__mono diff__before">{{ row.before ?? '—' }}</td>
        <td class="table__mono diff__after">
          {{ row.after ?? '—' }}
          <span v-if="row.unchanged" class="diff__unchanged">（不变）</span>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.diff__before {
  color: var(--color-danger);
  text-decoration: line-through;
  text-decoration-color: rgba(220, 38, 38, 0.35);
}

.diff__after {
  color: var(--color-success);
}

.diff__unchanged {
  color: var(--color-text-muted);
  font-size: 10.5px;
  text-decoration: none;
}

.diff__empty {
  text-align: center;
  color: var(--color-text-muted);
  padding: 24px 12px;
}
</style>
