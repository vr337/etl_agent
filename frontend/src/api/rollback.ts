/**
 * M-P 前端控制台 — 受管回滚 API（FR-45~48 / API-13）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 6.1 章 API-13
 * 覆盖 FR：FR-45~48（回滚申请 / 审批 / 执行 / 清理）
 * 对应 AC：AC-08（硬中断 + 根因诊断 + 受管回滚）
 */
import client from './client'
import type { CleanupScanResponse, RollbackListView, RollbackView } from './types'

export async function listRollbacks(
  projectId: string,
  runId: string,
): Promise<RollbackListView[]> {
  const { data } = await client.get<RollbackListView[]>(
    `/projects/${projectId}/executions/${runId}/rollbacks`,
  )
  return data
}

export async function getRollback(rollbackId: string): Promise<RollbackView> {
  const { data } = await client.get<RollbackView>(`/rollbacks/${rollbackId}`)
  return data
}

export async function decideRollback(
  rollbackId: string,
  payload: { role: string; decision: 'APPROVE' | 'REJECT'; reason: string },
): Promise<RollbackView> {
  const { data } = await client.post<RollbackView>(`/rollbacks/${rollbackId}/decisions`, payload)
  return data
}

export async function scanCleanup(projectId: string): Promise<CleanupScanResponse> {
  const { data } = await client.post<CleanupScanResponse>(
    `/projects/${projectId}/rollbacks/cleanup-scan`,
  )
  return data
}
