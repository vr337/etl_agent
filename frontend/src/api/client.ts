/**
 * M-P 前端控制台 — HTTP 客户端（axios 实例 + 身份头注入 + 统一错误处理）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 6.4 章错误码 /
 *   第 4.2 章环境差异（dev 行）
 * 覆盖 FR：FR-58（统一经 X-Actor-ID 注入身份；dev-header 仅限非生产）
 * 对应 AC：AC-11（生产环境禁直传身份头）、AC-02（响应零明文）
 *
 * 安全红线（禁止事项 6）：
 * - `X-Actor-ID` **只在开发/非生产构建**注入；
 * - 生产构建（`import.meta.env.PROD`）一律不注入——生产身份由 trusted-gateway
 *   在网关注入 HMAC 签名身份（FR-65），前端直传等于伪造身份。
 */
import axios, { AxiosError, type AxiosInstance } from 'axios'

import { useNotificationStore } from '@/stores/notification'

declare module 'axios' {
  // 容错标记：调用方已显式 catch 的预期失败（如「空工作区无证据流 → 未验证/空态」），
  // 全局错误拦截器据此跳过红色通知——错误仍 reject，由调用方分支处理（不静默吞错）。
  export interface AxiosRequestConfig {
    silent?: boolean
  }
}

/** 后端统一错误体（SPEC 6.4：`{code, message, trace_id}`）。 */
export interface ApiErrorBody {
  code: string
  message: string
  trace_id?: string
}

export const ACTOR_STORAGE_KEY = 'actor_id'
export const PROJECT_STORAGE_KEY = 'project_id'

/**
 * 判断当前是否允许以 dev-header 注入身份。
 *
 * 生产构建恒为 false（FR-58 / AC-11 红线）；开发构建仍要求显式声明
 * `VITE_ALLOW_ACTOR_HEADER=true`，避免「本地起生产模式预览」时误注入。
 */
export function isActorHeaderAllowed(): boolean {
  if (import.meta.env.PROD) {
    return false
  }
  return import.meta.env.VITE_ALLOW_ACTOR_HEADER !== 'false'
}

export function getActorId(): string | null {
  return localStorage.getItem(ACTOR_STORAGE_KEY)
}

export function setActorId(actorId: string): void {
  localStorage.setItem(ACTOR_STORAGE_KEY, actorId)
}

export function getProjectId(): string | null {
  return localStorage.getItem(PROJECT_STORAGE_KEY)
}

export function setProjectId(projectId: string): void {
  localStorage.setItem(PROJECT_STORAGE_KEY, projectId)
}

const client: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

client.interceptors.request.use((config) => {
  // 仅在允许的环境注入（生产构建永不注入，见文件头安全红线）
  if (isActorHeaderAllowed()) {
    const actorId = getActorId()
    if (actorId) {
      config.headers.set('X-Actor-ID', actorId)
    }
  }
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorBody>) => {
    if (error.config?.silent) {
      // 调用方已显式容错（如「空工作区无证据流 → 显示未验证/空态」）：
      // 错误即业务分支，弹全局红色通知反而制造噪声。仍 reject，交由调用方分支处理。
      return Promise.reject(error)
    }
    const body = error.response?.data
    const notifications = useNotificationStore()
    if (body?.code) {
      // 展示 code + trace_id，让用户能直接拿去对日志（SPEC 6.4 / FR-60）
      const suffix = body.trace_id ? ` · trace_id=${body.trace_id}` : ''
      notifications.error(`${body.code}：${body.message}${suffix}`)
    } else if (error.code === 'ECONNABORTED') {
      notifications.error('请求超时：后端未在 30s 内响应')
    } else {
      notifications.error(`网络错误：${error.message}`)
    }
    return Promise.reject(error)
  },
)

/** 提取后端错误体（供页面按 code 分支展示，如 404 不泄露存在性）。 */
export function extractApiError(error: unknown): ApiErrorBody | null {
  if (axios.isAxiosError(error)) {
    const body = error.response?.data as ApiErrorBody | undefined
    return body ?? null
  }
  return null
}

export default client
