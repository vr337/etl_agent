/**
 * M-P 前端控制台 — 审计证据账本 API（FR-36）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 6.1 章 API
 * 覆盖 FR：FR-36（证据账本查询与整流验证）
 * 对应 AC：AC-09（证据可验证）
 *
 * 展示红线（禁止事项 16）：前端只展示 `sequence / occurred_at / event_type /
 * actor / risk / policy_decision_id / event_hash`，**不展示 hash 计算细节**
 * （不解释 SHA256 输入拼接、不提供链重算入口）——那是后端 `verify` 的职责。
 */
import client from './client'
import type { EvidenceEventView, EvidenceStreamView, VerifyResponse } from './types'

export async function getEvidenceStream(projectId: string): Promise<EvidenceStreamView> {
  const { data } = await client.get<EvidenceStreamView>(`/projects/${projectId}/evidence-stream`)
  return data
}

export async function listEvidenceEvents(
  projectId: string,
  params: { afterSequence?: number; limit?: number } = {},
): Promise<EvidenceEventView[]> {
  const { data } = await client.get<{ events: EvidenceEventView[] }>(
    `/projects/${projectId}/evidence-stream/events`,
    {
      params: {
        after_sequence: params.afterSequence ?? 0,
        limit: params.limit ?? 100,
      },
    },
  )
  return data.events
}

export async function verifyEvidenceStream(projectId: string): Promise<VerifyResponse> {
  const { data } = await client.get<VerifyResponse>(
    `/projects/${projectId}/evidence-stream/verify`,
  )
  return data
}
