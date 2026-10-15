/**
 * M-P 前端控制台 — 元数据探查与数据分级 API（FR-12/14/15）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-D / 第 6.1 章 API
 * 覆盖 FR：FR-12/14/15（表级探查与 Profile、脱敏样本）
 * 对应 AC：AC-02（脱敏样本无明文）
 */
import client from './client'
import type { ProfileView } from './types'

export async function listProfiles(projectId: string): Promise<ProfileView[]> {
  const { data } = await client.get<ProfileView[]>(`/projects/${projectId}/profiles`)
  return data
}

export async function probeTable(
  projectId: string,
  payload: {
    connection_id: string
    schema_name: string
    table_name: string
    endpoint_role: 'SOURCE' | 'SINK'
    sample_limit: number
  },
): Promise<ProfileView> {
  const { data } = await client.post<ProfileView>(`/projects/${projectId}/profiles`, payload)
  return data
}
