<script setup lang="ts">
/**
 * M-P 前端控制台 — 证据账本事件流（FR-36 / 原型图页面 5「哈希事件流」）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 7 章（页面 5）
 * 覆盖 FR：FR-36（证据账本：只追加 + 整流验证）
 * 对应 AC：AC-09（证据可验证）
 *
 * **展示红线（禁止事项 16）**：只展示 `sequence / event_type / actor / risk /
 * policy_decision_id / event_hash` 这些**事实字段**；
 * **不展示 hash 计算细节**（不解释 `SHA256(previous_hash‖payload_sha256‖sequence)`、
 * 不提供「重算链」按钮、不展示 payload 正文）——验证是后端 `verify` 的职责。
 */
import type { EvidenceEventView } from '@/api/types'
import RiskBadge from './RiskBadge.vue'

defineProps<{
  events: EvidenceEventView[]
  verifyValid?: boolean | null
  verifySummary?: string | null
}>()
</script>

<template>
  <div class="ev">
    <table class="table">
      <thead>
        <tr>
          <th style="width: 88px">sequence</th>
          <th>event_type</th>
          <th style="width: 110px">actor</th>
          <th style="width: 72px">risk</th>
          <th style="width: 140px">policy_decision_id</th>
          <th style="width: 110px">event_hash</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="events.length === 0">
          <td colspan="6" class="ev__empty">证据流尚无可展示事件</td>
        </tr>
        <tr v-for="event in events" v-else :key="event.id">
          <td class="mono">{{ event.sequence }}</td>
          <td class="mono">{{ event.event_type }}</td>
          <td>{{ event.actor_id?.slice(0, 12) ?? '—' }}</td>
          <td><RiskBadge v-if="event.risk_level" :level="event.risk_level" /></td>
          <td class="mono muted">{{ event.policy_decision_id?.slice(0, 12) ?? '—' }}</td>
          <td class="mono muted">{{ event.event_hash.slice(0, 8) }}…</td>
        </tr>
      </tbody>
    </table>

    <p v-if="verifySummary" class="ev__foot">
      <span class="badge" :class="verifyValid ? 'badge--success' : 'badge--danger'">
        valid = {{ verifyValid ? 'true' : 'false' }}
      </span>
      <span class="ev__sum mono">{{ verifySummary }}</span>
    </p>
  </div>
</template>

<style scoped>
.ev__empty {
  text-align: center;
  color: var(--color-text-muted);
  padding: 22px 12px;
}

.ev__foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 0;
}

.ev__sum {
  font-size: 10.5px;
  color: var(--color-text-muted);
}
</style>
