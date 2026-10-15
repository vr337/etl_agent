/**
 * M-P 前端控制台 — Pipeline 生成（AgentRun）API（FR-16/17）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 6.1 章 API-05/06 /
 *   第 7.1 章主流程时序
 * 覆盖 FR：FR-16/17（自然语言生成 + LangGraph 澄清中断/恢复，预算 ≤3）
 * 对应 AC：AC-03（中断与恢复）、AC-04（有限修复预算 ≤3）
 */
import client from './client'
import type { AgentRunView } from './types'

export async function listAgentRuns(projectId: string): Promise<AgentRunView[]> {
  const { data } = await client.get<AgentRunView[]>(`/projects/${projectId}/agent-runs`)
  return data
}

export async function createAgentRun(
  projectId: string,
  payload: {
    requirement: string
    source_profile_id: string
    sink_profile_id: string
    pipeline_id?: string
  },
): Promise<AgentRunView> {
  const { data } = await client.post<AgentRunView>(`/projects/${projectId}/agent-runs`, payload)
  return data
}

export async function sendAgentMessage(
  runId: string,
  payload: { answer: string },
): Promise<AgentRunView> {
  const { data } = await client.post<AgentRunView>(`/agent-runs/${runId}/messages`, payload)
  return data
}
