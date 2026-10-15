<script setup lang="ts">
/**
 * M-P 前端控制台 — 工作区初始化（Bootstrap，FR-58）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-B/M-P / 第 6.1 章 API-01 /
 *   第 4.2 章环境差异（dev-header 仅非生产）
 * 覆盖 FR：FR-58（工作区初始化引导与身份注入）
 * 对应 AC：**AC-11**（生产环境禁直传身份头；dev 走 X-Actor-ID）
 *
 * 后端契约（`POST /api/v1/bootstrap`，201）：
 * - Header 携带 `X-Bootstrap-Token`（**不是** body —— 见 projects/router.py）；
 * - Body = `{ project_name, admin_subject }`；
 * - 幂等约束：已有项目 → 409（禁止重复初始化）。
 *
 * 完成后把 `project_id` 与 `admin_user_id`（作为 actor）写入本地，
 * 后续请求由 client 在**非生产**环境下注入 `X-Actor-ID`。
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import client, { isActorHeaderAllowed } from '@/api/client'
import type { BootstrapResponse } from '@/api/types'
import { useActorStore } from '@/stores/actor'
import { useNotificationStore } from '@/stores/notification'

const router = useRouter()
const actor = useActorStore()
const notifications = useNotificationStore()

const projectName = ref('演示工作区')
const adminSubject = ref('admin@demo.local')
const bootstrapToken = ref('')
const submitting = ref(false)
const done = ref(false)
const result = ref<BootstrapResponse | null>(null)
const devHeaderAllowed = isActorHeaderAllowed()

/** Bootstrap Token 走 header（后端只读 `X-Bootstrap-Token`）。 */
async function onSubmit() {
  if (!projectName.value || !adminSubject.value) {
    notifications.error('项目名与管理员 subject 均为必填')
    return
  }
  submitting.value = true
  try {
    const { data } = await client.post<BootstrapResponse>(
      '/bootstrap',
      { project_name: projectName.value, admin_subject: adminSubject.value },
      { headers: bootstrapToken.value ? { 'X-Bootstrap-Token': bootstrapToken.value } : {} },
    )
    result.value = data
    done.value = true
    // X-Actor-ID 的语义是 subject（`actor_resolver.resolve_user` 按 User.subject 查表），
    // 不是 user_id —— admin_user_id 是 UUID 主键，注入它会导致 401 AUTH_401_UNKNOWN_ACTOR。
    // 此处以登录主体 admin_subject 作为初始 actor：后续经 X-Actor-ID 注入（仅非生产）。
    actor.bind({
      actorId: adminSubject.value,
      projectId: data.project_id,
      name: adminSubject.value,
      role: 'ADMIN',
    })
    notifications.success('工作区初始化完成')
  } catch {
    // 错误已由 client 拦截器统一提示（409 → BIZ_409 已初始化）
  } finally {
    submitting.value = false
  }
}

function enterConsole() {
  router.push('/dashboard')
}

function reuseExisting() {
  if (!actor.projectId || !actor.actorId) {
    notifications.error('请先填写并完成初始化')
    return
  }
  router.push('/dashboard')
}
</script>

<template>
  <div class="boot">
    <div class="boot__panel card">
      <div class="boot__brand">
        <div class="boot__logo">ETL.Agent</div>
        <div class="boot__tag">治理型 ETL 平台</div>
      </div>

      <h1 class="boot__title">工作区初始化</h1>
      <p class="boot__desc">
        初始化一个项目工作区并登记首位管理员身份。完成后控制台以该身份
        {{ devHeaderAllowed ? '经 X-Actor-ID 注入' : '经网关身份注入' }}发起请求。
      </p>

      <div v-if="!done" class="boot__form">
        <div class="field">
          <label class="field__label">项目名称</label>
          <input v-model="projectName" class="field__input" placeholder="演示工作区" />
        </div>
        <div class="field">
          <label class="field__label">管理员 subject</label>
          <input v-model="adminSubject" class="field__input" placeholder="admin@demo.local" />
        </div>
        <div class="field">
          <label class="field__label">Bootstrap Token <span class="muted">（经请求头携带）</span></label>
          <input
            v-model="bootstrapToken"
            type="password"
            class="field__input"
            placeholder="ENV: BOOTSTRAP_TOKEN（比对失败 403）"
          />
        </div>

        <button class="btn btn--primary boot__submit" :disabled="submitting" @click="onSubmit">
          {{ submitting ? '初始化中…' : '初始化工作区' }}
        </button>

        <p class="boot__note">
          Bootstrap 幂等：工作区已存在时返回 409，不会重复创建；Token 由服务端
          <span class="mono">BOOTSTRAP_TOKEN</span> 环境变量比对。
        </p>
      </div>

      <div v-else class="boot__done">
        <div class="boot__ok">✓ 工作区已就绪</div>
        <table class="table plain">
          <tbody>
            <tr>
              <td class="k">project_id</td>
              <td class="mono">{{ result?.project_id }}</td>
            </tr>
            <tr>
              <td class="k">admin_user_id</td>
              <td class="mono">{{ result?.admin_user_id }}</td>
            </tr>
            <tr>
              <td class="k">身份注入模式</td>
              <td class="mono">
                {{ devHeaderAllowed ? 'X-Actor-ID（非生产 dev-header）' : 'trusted-gateway HMAC（生产）' }}
              </td>
            </tr>
          </tbody>
        </table>
        <button class="btn btn--primary boot__submit" @click="enterConsole">进入控制台</button>
      </div>

      <div class="boot__foot">
        <span
          class="badge"
          :class="devHeaderAllowed ? 'badge--warn' : 'badge--success'"
        >
          {{ devHeaderAllowed ? '非生产：允许 dev-header 身份' : '生产：禁直传身份头（AC-11）' }}
        </span>
        <button class="btn btn--ghost btn--sm" @click="reuseExisting">沿用本地工作区</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.boot {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(1200px 640px at 18% 0%, #eef4ff 0%, transparent 60%),
    linear-gradient(180deg, #f7f9fc 0%, #eef1f7 100%);
  padding: 32px 16px;
}

.boot__panel {
  width: 100%;
  max-width: 520px;
  padding: 28px 30px 22px;
}

.boot__brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 22px;
}

.boot__logo {
  font-size: 15px;
  font-weight: 750;
  letter-spacing: -0.02em;
  color: var(--color-nav-bg);
}

.boot__tag {
  font-size: 10.5px;
  color: var(--color-text-muted);
}

.boot__title {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.boot__desc {
  margin: 0 0 20px;
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.boot__form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.boot__submit {
  width: 100%;
  justify-content: center;
  padding: 10px;
  margin-top: 4px;
}

.boot__note {
  margin: 2px 0 0;
  font-size: 10.5px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.boot__done {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.boot__ok {
  font-size: 13px;
  font-weight: 650;
  color: var(--color-success);
}

.table.plain td {
  padding: 6px 0;
  border-bottom: 1px dashed var(--color-border);
  font-size: 11px;
}

.table.plain tr:last-child td {
  border-bottom: none;
}

.table.plain .k {
  color: var(--color-text-muted);
  width: 38%;
}

.boot__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 20px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border);
}
</style>
