/**
 * 版本 Diff → 表格行的映射（唯一实现，Studio 与 Diff 视图共用）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H（版本 Diff）/ 第 6.1 章 API-07
 * 覆盖 FR：FR-27（不同版本的 Schema / HOCON / 质量规则差异对比）
 * 对应 AC：AC-17（版本 Diff 视图）
 *
 * 口径（`fix/S10-console-contract-a`）：后端 `VersionDiffResponse` 回的是
 * - `schema_diff` = `{added: string[], removed: string[], type_changed: {name,before,after}[]}`
 * - `quality_contract_diff` = `{added: string[], removed: string[], changed: {rule,before,after}[]}`
 * - `hocon_diff` = `{changed, added_lines, removed_lines, before_line_count, after_line_count}`
 *   （**只有行级计数，不回正文**——正文留在 `etl_checkpoint`，FR-67）
 *
 * 前端曾按 `schema_changes[] / hocon_changes[] / quality_changes[]` 读**不存在的数组**
 * → 变更数恒 0、表格恒空（AC-17 不可验）。本模块是唯一的映射点，两个视图都走它。
 */
import type { DiffRow } from '@/components/SchemaDiff.vue'
import type { VersionDiffView } from '@/api/types'

/** 三类正文级差异 → 表格行（`before/after` 为 `null` 表示新增/删除）。 */
export function toDiffRows(diff: VersionDiffView | null): DiffRow[] {
  if (!diff) return []
  const rows: DiffRow[] = []

  const schema = diff.schema_diff
  for (const name of schema?.added ?? []) {
    rows.push({ changeType: 'Schema', object: name, before: null, after: name })
  }
  for (const name of schema?.removed ?? []) {
    rows.push({ changeType: 'Schema', object: name, before: name, after: null })
  }
  for (const item of schema?.type_changed ?? []) {
    rows.push({ changeType: 'Schema', object: item.name, before: item.before, after: item.after })
  }

  const quality = diff.quality_contract_diff
  for (const key of quality?.added ?? []) {
    rows.push({ changeType: '质量规则', object: key, before: null, after: '（新增）' })
  }
  for (const key of quality?.removed ?? []) {
    rows.push({ changeType: '质量规则', object: key, before: '（已删除）', after: null })
  }
  for (const item of quality?.changed ?? []) {
    rows.push({
      changeType: '质量规则',
      object: item.rule,
      before: String(item.before ?? '—'),
      after: String(item.after ?? '—'),
    })
  }

  const hocon = diff.hocon_diff
  if (hocon?.changed) {
    rows.push({
      changeType: 'HOCON',
      object: '行级差异（不含正文）',
      before: `${hocon.before_line_count} 行`,
      after: `${hocon.after_line_count} 行（+${hocon.added_lines} / −${hocon.removed_lines}）`,
    })
  }
  return rows
}

/** 三类差异的条目数（页头徽标用）。 */
export function diffCounts(diff: VersionDiffView | null): {
  schema: number
  hocon: number
  quality: number
} {
  if (!diff) return { schema: 0, hocon: 0, quality: 0 }
  const schema = diff.schema_diff
  const quality = diff.quality_contract_diff
  return {
    schema:
      (schema?.added.length ?? 0) +
      (schema?.removed.length ?? 0) +
      (schema?.type_changed.length ?? 0),
    hocon: diff.hocon_diff?.changed ? 1 : 0,
    quality:
      (quality?.added.length ?? 0) +
      (quality?.removed.length ?? 0) +
      (quality?.changed.length ?? 0),
  }
}
