<script setup lang="ts">
/**
 * M-P 前端控制台 — 审批槽位卡（FR-56：数据审批人 / 安全审批人，「不可自批」）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J（四眼审批）/
 *   第 6.1 章 API-09 / 第 7 章（页面 4）
 * 覆盖 FR：FR-32（四眼审批：双唯一 + 不可自批）
 * 对应 AC：**AC-01**（职责分离：申请人禁自批，ADMIN 不隐式升级）
 *
 * 红线呈现：「不可自批」不是前端可关闭的开关——它是服务端 DB 双唯一约束的
 * **界面提示**。故本卡只展示后端返回的槽位事实，不提供「强制审批」入口。
 */
import type { ApprovalSlotView } from '@/api/types'
import StatusBadge from './StatusBadge.vue'

defineProps<{
  /** 审批槽位事实（由后端返回）。字段名避开 `slot`——Vue 中 `slot` 属性已废弃，
   *  作为 prop 名会触发 `vue/no-deprecated-slot-attribute`，故改名为 `slotView`。 */
  slotView: ApprovalSlotView
  disabled?: boolean
  hint?: string
}>()

const emit = defineEmits<{
  decide: [payload: { decision: 'APPROVE' | 'REJECT' }]
}>()

function stateOf(slotView: ApprovalSlotView): string {
  if (slotView.decided_by) {
    return slotView.decision === 'APPROVE' ? 'APPROVED' : 'REJECTED'
  }
  return 'WAITING_APPROVAL'
}
</script>

<template>
  <div class="slot">
    <div class="slot__head">
      <div>
        <div class="slot__role">{{ slotView.role }}</div>
        <div class="slot__meta">
          <template v-if="slotView.decided_by">
            {{ slotView.decided_by.slice(0, 8) }} · {{ slotView.decided_at?.slice(0, 16) ?? '' }}
            <span v-if="slotView.evidence_sha256" class="mono">
              · evidence {{ slotView.evidence_sha256.slice(0, 6) }}…
            </span>
          </template>
          <template v-else>{{ hint ?? '职责槽未占用，需一名安全审批人' }}</template>
        </div>
      </div>
      <StatusBadge :status="stateOf(slotView)" />
    </div>

    <div v-if="!slotView.decided_by" class="slot__actions">
      <button class="btn btn--primary btn--sm" :disabled="disabled" @click="emit('decide', { decision: 'APPROVE' })">
        批准
      </button>
      <button class="btn btn--sm" :disabled="disabled" @click="emit('decide', { decision: 'REJECT' })">
        驳回
      </button>
      <span v-if="disabled" class="slot__warn">申请人禁自批（DB 双唯一约束，ADMIN 不隐式升级）</span>
    </div>
  </div>
</template>

<style scoped>
.slot {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 11px 13px;
  background: #fcfdff;
}

.slot__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.slot__role {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text);
}

.slot__meta {
  font-size: 11px;
  color: var(--color-text-muted);
  margin-top: 2px;
}

.slot__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.slot__warn {
  font-size: 10.5px;
  color: var(--color-warn);
}
</style>
