/**
 * M-P 前端控制台 — 版本管理 API（FR-26/27：冻结 / 派生 / Diff）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H / 第 6.1 章 API-07
 * 覆盖 FR：FR-26（版本冻结）、FR-27（版本 Diff：Schema/HOCON/质量规则差异）
 * 对应 AC：AC-17（版本 Diff 视图）、AC-09（门禁全过才可冻结）
 */
import client from './client'
import type { PipelineVersionView, VersionDiffView } from './types'

export async function getVersion(versionId: string): Promise<PipelineVersionView> {
  const { data } = await client.get<PipelineVersionView>(`/pipeline-versions/${versionId}`)
  return data
}

export async function freezeVersion(
  versionId: string,
  payload: { gate_results: Record<string, boolean>; schedule_cron?: string },
): Promise<PipelineVersionView> {
  const { data } = await client.post<PipelineVersionView>(
    `/pipeline-versions/${versionId}/freeze`,
    payload,
  )
  return data
}

export async function deriveVersion(
  versionId: string,
  payload: { reason: string },
): Promise<PipelineVersionView> {
  const { data } = await client.post<PipelineVersionView>(
    `/pipeline-versions/${versionId}/derive`,
    payload,
  )
  return data
}

/**
 * 两版本 Diff（AC-17）。
 *
 * 注意 `base`/`target` 顺序即语义：diff 表达「base → target 的变更」，
 * 反向对比须显式交换，不能靠后端猜。
 */
export async function diffVersions(
  targetVersionId: string,
  baseVersionId: string,
): Promise<VersionDiffView> {
  const { data } = await client.get<VersionDiffView>(
    `/pipeline-versions/${targetVersionId}/diff`,
    { params: { base_version_id: baseVersionId } },
  )
  return data
}
