<script setup lang="ts">
/**
 * M-P 前端控制台 — 运行监督快照时间线（FR-56：预算水位 + 命中规则 + 减损动作）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L（冻结预算与 6 条规则）/
 *   第 7 章（页面 4）
 * 覆盖 FR：FR-45（运行时监督快照）
 * 对应 AC：**AC-08**（监督规则命中 → 减损动作）
 *
 * 还原口径（原型图）：每条预算一行「标签 + 数值 + 水位条」，命中项以告警色高亮；
 * 水位条比例 = 已用 / 上限（放大比 >1 时截断显示为满格 + 数值仍显真实值）。
 */
export interface BudgetRow {
  label: string
  display: string
  /** 水位 0–1（放大比等无上界指标由调用方裁剪） */
  ratio: number
  hit?: boolean
}

defineProps<{
  title?: string
  rows: BudgetRow[]
  action?: string | null
  risk?: string | null
}>()

function widthOf(ratio: number): string {
  const clamped = Math.max(0, Math.min(1, ratio))
  return `${Math.round(clamped * 100)}%`
}
</script>

<template>
  <div class="sup">
    <div v-for="row in rows" :key="row.label" class="sup__row">
      <div class="sup__label">{{ row.label }}</div>
      <div class="sup__track">
        <div
          class="sup__fill"
          :class="{ 'sup__fill--hit': row.hit }"
          :style="{ width: widthOf(row.ratio) }"
        ></div>
      </div>
      <div class="sup__value" :class="{ 'sup__value--hit': row.hit }">{{ row.display }}</div>
    </div>

    <p v-if="action" class="sup__footer">
      预警比例 0.8 触发 THROTTLE / PAUSE；超限触发 CANCEL / QUARANTINE，阻断发布与 Swap。
      <span class="mono">当前动作：{{ action }}<template v-if="risk"> · 风险 {{ risk }}</template></span>
    </p>
  </div>
</template>

<style scoped>
.sup {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sup__row {
  display: grid;
  grid-template-columns: 108px 1fr 62px;
  align-items: center;
  gap: 10px;
}

.sup__label {
  font-size: 11.5px;
  color: var(--color-text-secondary);
}

.sup__track {
  height: 7px;
  border-radius: 999px;
  background: var(--color-neutral-soft);
  overflow: hidden;
}

.sup__fill {
  height: 100%;
  border-radius: 999px;
  background: var(--color-primary);
  transition: width 0.3s;
}

.sup__fill--hit {
  background: var(--color-danger);
}

.sup__value {
  font-size: 12px;
  font-weight: 600;
  text-align: right;
  color: var(--color-text);
  font-variant-numeric: tabular-nums;
}

.sup__value--hit {
  color: var(--color-danger);
}

.sup__footer {
  font-size: 10.5px;
  color: var(--color-text-muted);
  margin: 4px 0 0;
  line-height: 1.7;
}
</style>
