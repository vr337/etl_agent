/**
 * M-P 前端控制台 — 认证与工作区初始化 API（FR-58）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 6.1 章 API
 * 覆盖 FR：FR-58（工作区初始化引导与身份注入）
 * 对应 AC：AC-11（生产禁直传身份头）
 */
import client from './client'
import type { BootstrapRequest, BootstrapResponse, MemberView, RoleGrantView } from './types'

export async function bootstrapWorkspace(payload: BootstrapRequest): Promise<BootstrapResponse> {
  const { data } = await client.post<BootstrapResponse>('/bootstrap', payload)
  return data
}

export async function listMembers(projectId: string): Promise<MemberView[]> {
  const { data } = await client.get<MemberView[]>(`/projects/${projectId}/members`)
  return data
}

export async function listRoleGrants(projectId: string): Promise<RoleGrantView[]> {
  const { data } = await client.get<RoleGrantView[]>(`/projects/${projectId}/role-grants`)
  return data
}

export async function grantRole(
  projectId: string,
  userId: string,
  role: string,
): Promise<RoleGrantView> {
  const { data } = await client.post<RoleGrantView>(`/projects/${projectId}/role-grants`, {
    user_id: userId,
    role,
  })
  return data
}
