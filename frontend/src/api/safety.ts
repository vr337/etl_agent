/**
 * M-P 前端控制台 — 安全改进提案与受控发布 API（FR-51/52）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.1 章 API-15 族
 * 覆盖 FR：FR-51（安全改进提案状态机）、FR-52（受控发布：影子→小流量→晋升/回滚）
 * 对应 AC：AC-01（仅 SECURITY_APPROVER 可裁决）
 *
 * 状态机红线：提案状态机不可跳跃（DRAFT→IN_REVIEW→APPROVED/REJECTED→RELEASED）；
 * 前端只发起「合法下一步」，越级由后端 409 拒绝（前端不做状态机复制）。
 */
import client from './client'
import type {
  ProposalDecisionRequest,
  ProposalView,
  ReleaseView,
  ObservationView,
} from './types'

export async function listProposals(projectId: string): Promise<ProposalView[]> {
  const { data } = await client.get<{ proposals: ProposalView[]; total: number }>(
    `/projects/${projectId}/safety-proposals`,
  )
  return data.proposals
}

export async function submitProposal(proposalId: string): Promise<ProposalView> {
  const { data } = await client.post<ProposalView>(`/safety-proposals/${proposalId}/submit`)
  return data
}

export async function decideProposal(
  proposalId: string,
  payload: ProposalDecisionRequest,
): Promise<ProposalView> {
  const { data } = await client.post<ProposalView>(
    `/safety-proposals/${proposalId}/decisions`,
    payload,
  )
  return data
}

export async function createRelease(
  proposalId: string,
  payload: { canary_percent: number },
): Promise<ReleaseView> {
  // 后端 POST 在**复数**路径（`/safety-proposals/{id}/releases`）；
  // 单数 `/release` 只提供 GET（读取当前发布态），POST 会得到 405。
  const { data } = await client.post<ReleaseView>(
    `/safety-proposals/${proposalId}/releases`,
    payload,
  )
  return data
}

export async function listReleases(proposalId: string): Promise<ReleaseView[]> {
  const { data } = await client.get<{ releases: ReleaseView[] }>(
    `/safety-proposals/${proposalId}/releases`,
  )
  return data.releases
}

export async function promoteRelease(releaseId: string): Promise<ReleaseView> {
  const { data } = await client.post<ReleaseView>(`/safety-releases/${releaseId}/promotion`)
  return data
}

export async function rollbackRelease(releaseId: string): Promise<ReleaseView> {
  const { data } = await client.post<ReleaseView>(`/safety-releases/${releaseId}/rollback`)
  return data
}

export type { ObservationView }
