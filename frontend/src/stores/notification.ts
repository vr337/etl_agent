/**
 * M-P 前端控制台 — 全局通知（Pinia）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 6.4 章错误码
 * 覆盖 FR：FR-60（错误响应附 trace_id，前端可见）
 * 对应 AC：AC-10
 *
 * 口径：只保留最近若干条并自动过期；通知栏展示 `code + trace_id` 原文，
 * 便于用户直接对日志（不解析、不美化后端错误码，避免两套词汇表）。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export type NotificationLevel = 'info' | 'success' | 'warn' | 'error'

export interface AppNotification {
  id: number
  level: NotificationLevel
  message: string
  at: number
}

const MAX_ITEMS = 4
const TTL_MS = 8000

export const useNotificationStore = defineStore('notification', () => {
  const items = ref<AppNotification[]>([])
  let seq = 0

  function push(level: NotificationLevel, message: string) {
    const id = ++seq
    items.value.push({ id, level, message, at: Date.now() })
    if (items.value.length > MAX_ITEMS) {
      items.value.shift()
    }
    window.setTimeout(() => dismiss(id), TTL_MS)
  }

  function dismiss(id: number) {
    items.value = items.value.filter((item) => item.id !== id)
  }

  const info = (message: string) => push('info', message)
  const success = (message: string) => push('success', message)
  const warn = (message: string) => push('warn', message)
  const error = (message: string) => push('error', message)

  return { items, push, dismiss, info, success, warn, error }
})
