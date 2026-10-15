/**
 * M-P 前端控制台 — Benchmark 评测 API（FR-50 / API-15）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.1 章 API-15 / 第 6.2 章
 * 覆盖 FR：FR-50（运行版本化 Benchmark 套件 + 六项门禁）
 * 对应 AC：AC-09（六项门禁）、AC-01（仅 SECURITY_APPROVER 可触发）
 *
 * 触发红线：API-15 仅 SECURITY_APPROVER 可调（403 AUTH_403_ROLE_REQUIRED）——
 * 前端不在本地判断角色，由后端裁决（前端判断只是提前反馈，不是授权）。
 */
import client from './client'
import type { BenchmarkRunView } from './types'

export async function listBenchmarkRuns(projectId: string): Promise<BenchmarkRunView[]> {
  const { data } = await client.get<{ runs: BenchmarkRunView[]; total: number }>(
    `/projects/${projectId}/benchmark-runs`,
  )
  return data.runs
}

export async function getBenchmarkRun(
  projectId: string,
  runId: string,
): Promise<BenchmarkRunView> {
  const { data } = await client.get<BenchmarkRunView>(
    `/projects/${projectId}/benchmark-runs/${runId}`,
  )
  return data
}

export async function runBenchmark(
  projectId: string,
  payload: { suite_id: string; manifest_version: string; layers: string[] },
): Promise<BenchmarkRunView> {
  const { data } = await client.post<BenchmarkRunView>(
    `/projects/${projectId}/benchmark-runs`,
    payload,
  )
  return data
}
