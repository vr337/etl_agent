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

/**
 * 「空工作区无证据流」是预期分支（后端对未建流项目一律 404 且不自动建流）：
 * 三个读取函数均带 `silent` 容错标记——调用方（Dashboard / SafetyGovernance）
 * 已显式 catch 并走「未验证 / 空时间线」空态，全局拦截器不再重复弹红色通知。
 * 任一端点真的异常时，失败仍会 reject 到调用方的空态分支（不静默吞错）。
 */
export async function getEvidenceStream(projectId: string): Promise<EvidenceStreamView> {
  const { data } = await client.get<EvidenceStreamView>(`/projects/${projectId}/evidence-stream`, {
    silent: true,
  })
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
      silent: true,
    },
  )
  return data.events
}

export async function verifyEvidenceStream(projectId: string): Promise<VerifyResponse> {
  const { data } = await client.get<VerifyResponse>(
    `/projects/${projectId}/evidence-stream/verify`,
    { silent: true },
  )
  return data
}
