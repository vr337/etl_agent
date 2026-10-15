<script setup lang="ts">
/**
 * M-P 前端控制台 — 数据表（原型图各表格统一形态：表头 + 悬停行 + 空态）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 7 章（页面原型）
 * 覆盖 FR：FR-53~57（各页表格）
 * 对应 AC：AC-10（端到端演示链路）
 *
 * 泛型列定义：`key` 取记录字段名，`align` 控制数字列右对齐；
 * 单元格内容交给 `#cell-<key>` 插槽自定义（徽标 / 按钮 / 双行）。
 */
export interface Column {
  key: string
  label: string
  align?: 'left' | 'right'
  width?: string
}

defineProps<{
  columns: Column[]
  rows: Record<string, unknown>[]
  rowKey?: string
  emptyText?: string
  loading?: boolean
}>()
</script>

<template>
  <div class="table-wrap">
    <table class="table">
      <thead>
        <tr>
          <th
            v-for="col in columns"
            :key="col.key"
            :style="{ textAlign: col.align ?? 'left', width: col.width }"
          >
            {{ col.label }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading">
          <td :colspan="columns.length" class="table__empty">加载中…</td>
        </tr>
        <tr v-else-if="rows.length === 0">
          <td :colspan="columns.length" class="table__empty">{{ emptyText ?? '暂无数据' }}</td>
        </tr>
        <tr v-for="(row, index) in rows" v-else :key="String(row[rowKey ?? 'id'] ?? index)">
          <td
            v-for="col in columns"
            :key="col.key"
            :style="{ textAlign: col.align ?? 'left' }"
          >
            <slot :name="`cell-${col.key}`" :row="row" :value="row[col.key]">
              {{ row[col.key] ?? '—' }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.table-wrap {
  overflow-x: auto;
}

.table__empty {
  text-align: center;
  color: var(--color-text-muted);
  padding: 28px 12px;
  font-size: 12px;
}
</style>
