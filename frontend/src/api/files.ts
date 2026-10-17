/**
 * M-P 前端控制台 — 文件资产 API（FR-13）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E / 第 6.1 章 API
 * 覆盖 FR：FR-13（受管文件源导入：25 MiB / 100,000 行上限）
 * 对应 AC：AC-02（上传路径不落明文；本页只列元数据）
 */
import client from './client'
import type { FileAssetDetailView, FileAssetView } from './types'

export async function listFileAssets(projectId: string): Promise<FileAssetView[]> {
  const { data } = await client.get<FileAssetView[]>(`/projects/${projectId}/file-assets`)
  return data
}

export async function uploadFileAsset(
  projectId: string,
  file: File,
): Promise<FileAssetDetailView> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await client.post<FileAssetDetailView>(
    `/projects/${projectId}/file-assets`,
    form,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
    },
  )
  return data
}
