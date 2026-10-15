<script setup lang="ts">
/**
 * M-P 前端控制台 — 统计卡（5 张原型图顶部指标卡统一形态）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 7 章（页面原型）
 * 覆盖 FR：FR-53~57（各页顶部统计卡）
 * 对应 AC：AC-10（端到端演示链路）
 *
 * 还原口径：值用大号字、标签在上、补充说明在下（原型图 4 卡一排的排版）。
 */
defineProps<{
  label: string
  value: string | number
  hint?: string
  tone?: 'default' | 'primary' | 'success' | 'warn' | 'danger'
}>()
</script>

<template>
  <div class="stat card">
    <div class="stat__label">{{ label }}</div>
    <div class="stat__value" :class="tone && tone !== 'default' ? `stat__value--${tone}` : ''">
      {{ value }}
    </div>
    <div v-if="hint" class="stat__hint">{{ hint }}</div>
  </div>
</template>

<style scoped>
.stat {
  padding: 16px 18px;
}

.stat__label {
  font-size: 11.5px;
  color: var(--color-text-muted);
  margin-bottom: 6px;
}

.stat__value {
  font-size: 26px;
  font-weight: 650;
  line-height: 1.15;
  color: var(--color-text);
  letter-spacing: -0.01em;
}

.stat__value--primary {
  color: var(--color-primary);
}
.stat__value--success {
  color: var(--color-success);
}
.stat__value--warn {
  color: var(--color-warn);
}
.stat__value--danger {
  color: var(--color-danger);
}

.stat__hint {
  font-size: 11px;
  color: var(--color-text-muted);
  margin-top: 6px;
}
</style>
