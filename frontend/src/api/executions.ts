/**
 * M-P 前端控制台 — 执行监控与质量报告 API（FR-38/39 / API-11/12）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K/M-L / 第 6.1 章 API-11/12
 * 覆盖 FR：FR-38/39（执行监控与实时指标）、FR-43/44（质量报告）
 * 对应 AC：AC-06（执行与调度可观测）、AC-07（质量报告四态）
 *
 * 只读红线（禁止事项 17）：前端只读监控，不触发原子 Swap / 硬中断。
 */
import client from './client'
import type { ExecutionDetailView, ExecutionRunView, QualityReportView } from './types'

export async function listExecutions(projectId: string): Promise<ExecutionRunView[]> {
  const { data } = await client.get<ExecutionRunView[]>(`/projects/${projectId}/executions`)
  return data
}

export async function getExecution(runId: string): Promise<ExecutionDetailView> {
  const { data } = await client.get<ExecutionDetailView>(`/executions/${runId}`)
  return data
}

export async function getQualityReport(runId: string): Promise<QualityReportView> {
  const { data } = await client.get<QualityReportView>(`/executions/${runId}/quality-report`)
  return data
}
