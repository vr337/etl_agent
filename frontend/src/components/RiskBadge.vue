<script setup lang="ts">
/**
 * M-P 前端控制台 — 风险等级徽标（P0–P3，原型图「P0 · 需 2 人」等形态）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J（P0–P3 风险分级）
 * 覆盖 FR：FR-31（准备单风险等级）、FR-45（回滚风险等级）
 * 对应 AC：AC-01（职责分离：P0 需双人）
 *
 * 风险取色按原型图：P0 红 / P1 橙 / P2 蓝 / P3 灰。
 */
import { computed } from 'vue'

import type { RiskLevel } from '@/api/types'

const props = defineProps<{ level: RiskLevel | string | null | undefined; suffix?: string }>()

const tone = computed(() => {
  switch ((props.level ?? '').toUpperCase()) {
    case 'P0':
      return 'p0'
    case 'P1':
      return 'p1'
    case 'P2':
      return 'p2'
    case 'P3':
      return 'p3'
    default:
      return 'p3'
  }
})
</script>

<template>
  <span class="risk" :class="`risk--${tone}`">
    {{ level ?? '—' }}<template v-if="suffix"> · {{ suffix }}</template>
  </span>
</template>

<style scoped>
.risk {
  display: inline-flex;
  align-items: center;
  padding: 2px 9px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.risk--p0 {
  color: #fff;
  background: var(--risk-p0);
}
.risk--p1 {
  color: var(--color-warn);
  background: var(--color-warn-soft);
}
.risk--p2 {
  color: var(--color-info);
  background: var(--color-info-soft);
}
.risk--p3 {
  color: var(--color-neutral);
  background: var(--color-neutral-soft);
}
</style>
