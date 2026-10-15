<script setup lang="ts">
/**
 * M-P 前端控制台 — 质量报告卡（FR-56：总/通过/拒绝行数 + 拒绝率 + 错误码分布 + 诊断）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K（质量契约）/ 第 7 章（页面 4）
 * 覆盖 FR：FR-43/44（质量报告四态：PASSED/WARNING/FAILED/INCOMPLETE）
 * 对应 AC：**AC-07**（质量报告四态 + 拒绝率 + 诊断建议）
 *
 * 口径：报告只回**聚合**（总/通过/拒绝/错误码计数），不回原始被拒数据
 * ——错误行的原始值只落错误表，不进控制面（SPEC M-K 边界）。
 */
import { computed } from 'vue'

import type { QualityReportView } from '@/api/types'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ report: QualityReportView | null | undefined }>()

const errorCodes = computed(() => {
  const dist = props.report?.error_code_distribution ?? {}
  return Object.entries(dist).sort((a, b) => b[1] - a[1])
})

const rejectRatePercent = computed(() =>
  props.report ? (props.report.reject_rate * 100).toFixed(2) : '—',
)
</script>

<template>
  <div v-if="report" class="quality">
    <div class="quality__head">
      <span class="quality__title">质量报告</span>
      <StatusBadge :status="report.status" />
    </div>

    <div class="quality__grid">
      <div class="quality__cell">
        <div class="quality__k">总行数</div>
        <div class="quality__v">{{ report.total_rows.toLocaleString() }}</div>
      </div>
      <div class="quality__cell">
        <div class="quality__k">通过</div>
        <div class="quality__v quality__v--ok">{{ report.passed_rows.toLocaleString() }}</div>
      </div>
      <div class="quality__cell">
        <div class="quality__k">拒绝</div>
        <div class="quality__v quality__v--bad">{{ report.rejected_rows.toLocaleString() }}</div>
      </div>
      <div class="quality__cell">
        <div class="quality__k">拒绝率</div>
        <div class="quality__v">{{ rejectRatePercent }}%</div>
      </div>
    </div>

    <div v-if="errorCodes.length" class="quality__codes">
      <div class="quality__k">错误码分布</div>
      <div class="quality__chips">
        <span v-for="[code, count] in errorCodes" :key="code" class="chip mono">
          {{ code }} <b>{{ count }}</b>
        </span>
      </div>
    </div>

    <ul v-if="report.diagnostics.length" class="quality__diag">
      <li v-for="(item, index) in report.diagnostics" :key="index">{{ item }}</li>
    </ul>
  </div>
  <div v-else class="quality__empty">暂无质量报告（运行完成后生成）</div>
</template>

<style scoped>
.quality {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  background: #fcfdff;
}

.quality__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.quality__title {
  font-size: 12.5px;
  font-weight: 600;
}

.quality__grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.quality__k {
  font-size: 10.5px;
  color: var(--color-text-muted);
}

.quality__v {
  font-size: 16px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.quality__v--ok {
  color: var(--color-success);
}
.quality__v--bad {
  color: var(--color-danger);
}

.quality__codes {
  margin-top: 12px;
}

.quality__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.chip {
  font-size: 10.5px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--color-neutral-soft);
  color: var(--color-text-secondary);
}

.quality__diag {
  margin: 12px 0 0;
  padding-left: 16px;
  font-size: 11.5px;
  color: var(--color-text-secondary);
  line-height: 1.75;
}

.quality__empty {
  color: var(--color-text-muted);
  font-size: 12px;
  padding: 20px;
  text-align: center;
}
</style>
