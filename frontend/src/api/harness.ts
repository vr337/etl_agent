/**
 * M-P 前端控制台 — Harness 准备单与四眼审批 API（FR-31~33 / API-08~10）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 6.1 章 API-08~10 /
 *   第 7.1 章主流程时序
 * 覆盖 FR：FR-31（准备单）、FR-32（四眼审批）、FR-33（Commit 签发 Capability）
 * 对应 AC：AC-05（Harness 三阶段协议）、AC-01（职责分离：不可自批）
 */
import client from './client'
import type { CommitResponse, DecisionRequest, PreparationView } from './types'

export async function listPreparations(projectId: string): Promise<PreparationView[]> {
  const { data } = await client.get<PreparationView[]>(`/projects/${projectId}/preparations`)
  return data
}

export async function createPreparation(
  projectId: string,
  payload: { pipeline_version_id: string; target_environment: string },
): Promise<PreparationView> {
  const { data } = await client.post<PreparationView>(
    `/projects/${projectId}/preparations`,
    payload,
  )
  return data
}

export async function decidePreparation(
  preparationId: string,
  payload: DecisionRequest,
): Promise<PreparationView> {
  const { data } = await client.post<PreparationView>(
    `/preparations/${preparationId}/decisions`,
    payload,
  )
  return data
}

export async function commitPreparation(preparationId: string): Promise<CommitResponse> {
  const { data } = await client.post<CommitResponse>(`/preparations/${preparationId}/commit`)
  return data
}
