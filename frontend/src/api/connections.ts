/**
 * M-P 前端控制台 — 连接与凭据治理 API（FR-04~08/11）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / 第 6.1 章 API-02/03
 * 覆盖 FR：FR-04~11（连接登记 / 连通性测试 / 凭据轮换）
 * 对应 AC：AC-02（凭据零明文：登记只传、响应只回摘要）
 */
import client from './client'
import type {
  ConnectionCreateRequest,
  ConnectionTestResponse,
  ConnectionView,
  RotationResponseView,
  RotationView,
} from './types'

export async function listConnections(projectId: string): Promise<ConnectionView[]> {
  const { data } = await client.get<ConnectionView[]>(`/projects/${projectId}/connections`)
  return data
}

export async function createConnection(
  projectId: string,
  payload: ConnectionCreateRequest,
): Promise<ConnectionView> {
  const { data } = await client.post<ConnectionView>(`/projects/${projectId}/connections`, payload)
  return data
}

export async function testConnection(
  projectId: string,
  connectionId: string,
): Promise<ConnectionTestResponse> {
  const { data } = await client.post<ConnectionTestResponse>(
    `/projects/${projectId}/connections/${connectionId}/test`,
  )
  return data
}

export async function startRotation(
  projectId: string,
  connectionId: string,
  payload: { new_username: string; new_password: string; reason: string },
): Promise<RotationResponseView> {
  const { data } = await client.post<RotationResponseView>(
    `/projects/${projectId}/connections/${connectionId}/rotations`,
    payload,
  )
  return data
}

export async function listRotations(
  projectId: string,
  connectionId: string,
): Promise<RotationView[]> {
  const { data } = await client.get<RotationView[]>(
    `/projects/${projectId}/connections/${connectionId}/rotations`,
  )
  return data
}
