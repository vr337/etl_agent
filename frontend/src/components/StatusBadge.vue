<script setup lang="ts">
/**
 * M-P 前端控制台 — 状态徽标（原型图各表格「状态」列的彩色圆点 + 文案）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 7 章（页面原型）
 * 覆盖 FR：FR-54（连接状态）/ FR-56（执行状态）
 * 对应 AC：AC-10（端到端演示链路）
 *
 * 状态 → 色调映射（按原型图取色）：
 * - 成功/可用：SUCCEEDED / READY / PASSED / APPROVED / APPLIED / ACTIVE
 * - 进行中：RUNNING / TESTING / IN_REVIEW / CANARY / SHADOW_RUNNING / STAGING
 * - 告警：QUARANTINED / WARNING / WAITING_APPROVAL / WAITING_INPUT / PENDING / DRAFT
 * - 失败：FAILED / REJECTED / CANCELLED / ROLLED_BACK
 * - 未知/中性：UNTESTED / N/A
 */
import { computed } from 'vue'

const props = defineProps<{ status: string | null | undefined }>()

const SUCCESS = new Set([
  'SUCCEEDED',
  'READY',
  'PASS',
  'PASSED',
  'APPROVED',
  'APPLIED',
  'ACTIVE',
  'RELEASED',
  'PROMOTION_APPROVED',
  'EXECUTED',
  'VALID',
])
const RUNNING = new Set([
  'RUNNING',
  'TESTING',
  'IN_REVIEW',
  'CANARY',
  'SHADOW_RUNNING',
  'STAGING',
  'VALIDATING',
  'PROFILING',
  'GENERATING',
  'QUEUED',
  'SENDING',
])
const WARN = new Set([
  'QUARANTINED',
  'WARNING',
  'WAITING_APPROVAL',
  'WAITING_INPUT',
  'PENDING',
  'DRAFT',
  'INCOMPLETE',
  'DEFER',
  'REQUIRE_APPROVAL',
  'SKIPPED',
  'THROTTLE',
  'PAUSE',
])
const DANGER = new Set(['FAILED', 'REJECTED', 'CANCELLED', 'ROLLED_BACK', 'DENY', 'EXPIRED'])

const tone = computed(() => {
  const value = (props.status ?? '').toUpperCase()
  if (SUCCESS.has(value)) return 'success'
  if (RUNNING.has(value)) return 'info'
  if (WARN.has(value)) return 'warn'
  if (DANGER.has(value)) return 'danger'
  return 'neutral'
})

const label = computed(() => props.status ?? 'N/A')
</script>

<template>
  <span class="badge" :class="`badge--${tone}`">
    <span class="badge__dot"></span>{{ label }}
  </span>
</template>
