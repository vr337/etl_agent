/**
 * M-P 前端控制台 — 操作者与工作区上下文（Pinia）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 4.2 章环境差异
 * 覆盖 FR：FR-58（工作区初始化与身份注入）
 * 对应 AC：AC-11（生产禁直传身份头）
 *
 * 口径：`actor_id` / `project_id` 持久化到 localStorage——刷新后仍需保持
 * 「我是谁 / 在哪个工作区」，否则每次刷新都被踢回初始化页。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  ACTOR_ROLE_STORAGE_KEY,
  getActorId,
  getActorRole,
  getProjectId,
  setActorId,
  setActorRole,
  setProjectId,
} from '@/api/client'

export const useActorStore = defineStore('actor', () => {
  const actorId = ref<string | null>(getActorId())
  const projectId = ref<string | null>(getProjectId())
  // 刷新后从 localStorage 恢复显示名（actor_id 即 subject，与 bind() 传入的 name 同源）；
  // 此前仅 id/projectId 持久化、actorName 恒回初值「未登录」，导致 F5 后顶部栏身份丢失。
  const actorName = ref<string>(getActorId() ?? '未登录')
  // T-CONSOLE-01：职责一并持久化，否则 F5 后顶部栏恒显「未绑定职责」
  const actorRole = ref<string>(getActorRole() ?? '')

  function bind(actor: { actorId: string; projectId: string; name?: string; role?: string }) {
    actorId.value = actor.actorId
    projectId.value = actor.projectId
    setActorId(actor.actorId)
    setProjectId(actor.projectId)
    if (actor.name) {
      actorName.value = actor.name
    }
    if (actor.role) {
      actorRole.value = actor.role
      setActorRole(actor.role)
    }
    // 演示期把 subject 末段作为展示名（后端无「显示名」字段，避免臆造字段）
    if (!actor.name) {
      actorName.value = actor.actorId.slice(0, 8)
    }
  }

  function clear() {
    actorId.value = null
    projectId.value = null
    actorName.value = '未登录'
    actorRole.value = ''
    localStorage.removeItem('actor_id')
    localStorage.removeItem('project_id')
    localStorage.removeItem(ACTOR_ROLE_STORAGE_KEY)
  }

  const isReady = () => Boolean(actorId.value && projectId.value)

  return { actorId, projectId, actorName, actorRole, bind, clear, isReady }
})
