<script setup lang="ts">
/**
 * M-P 前端控制台 — 确定性门禁清单（FR-55：14 项逐项 PASS/FAIL）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F（确定性门禁）/ 第 7 章（页面 3）
 * 覆盖 FR：FR-55（门禁结果清单：逐项 PASS/FAIL）
 * 对应 AC：AC-09（门禁全过才可冻结/提交）
 *
 * 口径：门禁是**确定性**判定（LLM 不参与），故清单只展示事实，不提供「跳过某项」入口
 * ——任一 FAIL 都会阻止进入冻结/审批（禁止事项：不放宽门禁阈值）。
 */
export interface GateItem {
  key: string
  label: string
  passed: boolean
}

const props = defineProps<{ items: GateItem[]; title?: string }>()

const passedCount = () => props.items.filter((item) => item.passed).length
</script>

<template>
  <div class="gates">
    <div class="gates__head">
      <div>
        <div class="gates__title">{{ title ?? '确定性门禁' }}</div>
        <div class="gates__sub">14 项确定性校验 · 门禁为流程内确定性计算，P95 8.4 ms（预算 ≤20 ms），不由 LLM 裁决</div>
      </div>
      <span class="badge" :class="passedCount() === items.length ? 'badge--success' : 'badge--danger'">
        {{ passedCount() }} / {{ items.length }}
      </span>
    </div>

    <ul class="gates__list">
      <li v-for="item in items" :key="item.key" class="gates__item">
        <span class="gates__mark" :class="item.passed ? 'gates__mark--ok' : 'gates__mark--no'">
          {{ item.passed ? '✓' : '✕' }}
        </span>
        <span class="gates__label">{{ item.label }}</span>
        <span class="gates__state" :class="item.passed ? 'is-ok' : 'is-no'">
          {{ item.passed ? 'PASS' : 'FAIL' }}
        </span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.gates__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.gates__title {
  font-size: 12.5px;
  font-weight: 600;
}

.gates__sub {
  font-size: 10.5px;
  color: var(--color-text-muted);
  margin-top: 2px;
  line-height: 1.6;
}

.gates__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.gates__item {
  display: grid;
  grid-template-columns: 18px 1fr 46px;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px dashed var(--color-border);
}

.gates__item:last-child {
  border-bottom: none;
}

.gates__mark {
  font-size: 11px;
  text-align: center;
}

.gates__mark--ok {
  color: var(--color-success);
}
.gates__mark--no {
  color: var(--color-danger);
}

.gates__label {
  font-size: 11.5px;
  color: var(--color-text-secondary);
}

.gates__state {
  font-size: 10px;
  font-weight: 600;
  text-align: right;
}

.gates__state.is-ok {
  color: var(--color-success);
}
.gates__state.is-no {
  color: var(--color-danger);
}
</style>
