#!/usr/bin/env node
/**
 * 前端类型 ↔ 后端 response_model **契约门禁**。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.2 章接口契约 / 第 6.1 章 API 面
 * 覆盖 FR：FR-53~58（前端控制台）
 * 对应 AC：AC-10（端到端演示链路）、AC-17（版本 Diff 视图）
 *
 * ## 为什么需要这道门禁
 *
 * S10 收尾阶段系统扫描发现 **18 个 interface 与后端出参字段不匹配**
 * （T-CONSOLE-02 / 03 / 04 + `fix/S10-console-contract-a`）。共同根因不是零散笔误，而是
 * 「前端按**设想的**契约写类型」—— TypeScript 只校验前端内部自洽，**从不校验后端实际返回什么**。
 * 于是字段名写错（`version_no` vs `version_number`、`execution_run_id` vs `run_id`）时：
 * 编译通过、lint 通过、构建通过，**运行时字段恒 `undefined`**，再被 `?? 'CANARY'` 这类兜底
 * 伪装成「有数据且状态正常」。这类缺陷只能靠人肉读码发现，代价极高。
 *
 * 本脚本把这种漂移变成**可复现的失败**：从 OpenAPI 取后端真实 `response_model`，
 * 解析 `src/api/*.ts` 的调用点，逐字段比对 `types.ts` 的 interface。
 *
 * ## 用法
 *
 *   node scripts/check-contract.mjs                  # 默认：先试 http://127.0.0.1:8000，失败则离线生成
 *   node scripts/check-contract.mjs --api http://host:8000
 *   node scripts/check-contract.mjs --openapi ./openapi.json
 *   node scripts/check-contract.mjs --strict         # 「后端多出字段」也算失败
 *   node scripts/check-contract.mjs --json           # 机器可读
 *
 * ## 严重性口径（为什么默认不 strict）
 *
 * | 方向 | 含义 | 默认 |
 * | --- | --- | --- |
 * | **前端多出** | 前端声明了后端**不返回**的字段 → 运行时恒 `undefined`，最危险 | **失败** |
 * | 后端多出 | 后端回了前端没声明的字段 → 只是没用上，不会错 | 仅告警（`--strict` 时失败） |
 *
 * ## baseline（已知欠账）
 *
 * `scripts/contract-baseline.json` 记录**已知未修**的漂移指纹（`METHOD path → Interface`）。
 * 命中 baseline 的漂移只报告、不计失败 —— 这样门禁可以在存在分批偿还的技术债时先落地，
 * 同时**任何新增漂移立刻红灯**。修完一项就从 baseline 删一行（PR-B 负责清空）。
 *
 * ## 退出码
 *
 * - `0` 无新增漂移（baseline 内的已知项不算）
 * - `1` 存在**新增**字段漂移（或 `--strict` 下的后端多出）
 * - `2` 无法获取 OpenAPI / 未解析到任何调用点（**不静默跳过** —— 门禁失效比红灯更危险）
 *
 * ## CI 接入
 *
 * 无需起后端（`app.openapi()` 不依赖数据库）：
 *
 *   uv run python -c "import json,sys; from etl_agent.main import app; json.dump(app.openapi(), sys.stdout)" > openapi.json
 *   node frontend/scripts/check-contract.mjs --openapi openapi.json
 *
 * 或直接 `npm run check:contract`（自动：本地 API → 离线生成）。
 */
import { spawnSync } from 'node:child_process'
import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
/** 从 `frontend/scripts/` 上溯两级到仓库根（与 capture-screens.mjs 同口径）。 */
const REPO_ROOT = path.resolve(__dirname, '..', '..')
const API_DIR = path.join(REPO_ROOT, 'frontend', 'src', 'api')
const TYPES_FILE = path.join(API_DIR, 'types.ts')
const BASELINE_FILE = path.join(__dirname, 'contract-baseline.json')

/* ---------------- 参数 ---------------- */
const args = process.argv.slice(2)
const argOf = (name, fallback = null) => {
  const i = args.indexOf(name)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}
const STRICT = args.includes('--strict')
const AS_JSON = args.includes('--json')
const API_BASE = argOf('--api', 'http://127.0.0.1:8000')
const OPENAPI = argOf('--openapi', null)

/* ---------------- OpenAPI 获取 ---------------- */
const OFFLINE_GEN = `
import json, sys
from etl_agent.main import app
json.dump(app.openapi(), sys.stdout)
`

async function fromUrl(url) {
  const res = await fetch(url, { signal: AbortSignal.timeout(8000) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

function fromOffline() {
  const run = spawnSync('uv', ['run', 'python', '-c', OFFLINE_GEN], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  if (run.status !== 0) throw new Error(`离线生成失败：${(run.stderr || '').slice(0, 400)}`)
  return JSON.parse(run.stdout)
}

async function loadSpec() {
  if (OPENAPI) {
    if (/^https?:/.test(OPENAPI)) return fromUrl(OPENAPI)
    return JSON.parse(await readFile(path.resolve(REPO_ROOT, OPENAPI), 'utf8'))
  }
  try {
    return await fromUrl(`${API_BASE.replace(/\/$/, '')}/openapi.json`)
  } catch (err) {
    process.stderr.write(
      `[contract] 无法从 ${API_BASE}/openapi.json 取到 OpenAPI（${err.message}），改走离线生成…\n`,
    )
    return fromOffline()
  }
}

/* ---------------- 解析前端 ---------------- */

/**
 * `types.ts` → Map<interface 名, 字段名[]>。
 *
 * 支持 `interface X extends Y`（如 `ExecutionDetailView extends ExecutionRunView`）：
 * 继承来的字段一并计入，否则这类 interface 会被静默漏检。
 */
function parseInterfaces(text) {
  const raw = new Map()
  for (const m of text.matchAll(
    /export interface (\w+)(?:\s+extends\s+([\w, ]+))?\s*\{\n([\s\S]*?)\n\}/g,
  )) {
    const body = m[3]
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/[^\n]*/g, '')
    const fields = []
    for (const line of body.split('\n')) {
      const fm = line.match(/^\s*(\w+)\??\s*:\s*.+?,?\s*$/)
      if (fm) fields.push(fm[1])
    }
    const bases = (m[2] ?? '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    raw.set(m[1], { bases, fields })
  }

  const out = new Map()
  const resolve = (name, seen = new Set()) => {
    if (out.has(name)) return out.get(name)
    const entry = raw.get(name)
    if (!entry || seen.has(name)) return []
    seen.add(name)
    const inherited = entry.bases.flatMap((base) => resolve(base, seen))
    const merged = [...inherited, ...entry.fields]
    out.set(name, merged)
    return merged
  }
  for (const name of raw.keys()) resolve(name)
  return out
}

/** `src/api/*.ts` → 调用点列表（method / 泛型实参 / 路径）。 */
async function parseCalls() {
  const files = (await readdir(API_DIR)).filter(
    (name) => name.endsWith('.ts') && name !== 'types.ts' && name !== 'client.ts',
  )
  const calls = []
  for (const name of files) {
    const text = await readFile(path.join(API_DIR, name), 'utf8')
    // 懒匹配泛型实参，兼容 `{ runs: X[]; total: number }` 这类内联对象
    for (const m of text.matchAll(/client\.(get|post|put|patch|delete)<(.+?)>\(\s*[`'"]([^`'"]+)/g)) {
      calls.push({
        file: name,
        method: m[1].toUpperCase(),
        typeExpr: m[2].trim(),
        path: normPath(m[3]),
      })
    }
  }
  return calls
}

/** 路径归一：去 query、把 `{x}` / `${x}` 都折成 `{}`、去尾斜杠。 */
function normPath(raw) {
  return (
    raw
      .split('?')[0]
      .replace(/\{[^}]*\}/g, '{}')
      .replace(/\$\{[^}]*\}/g, '{}')
      .replace(/\/+$/, '') || '/'
  )
}

/* ---------------- 解析 OpenAPI ---------------- */
const stripApiPrefix = (p) => p.replace(/^\/api\/v1(?=\/|$)/, '') || '/'

function buildRouteIndex(spec) {
  const index = new Map()
  for (const [rawPath, item] of Object.entries(spec.paths ?? {})) {
    const key = normPath(stripApiPrefix(rawPath))
    for (const method of ['get', 'post', 'put', 'patch', 'delete']) {
      if (item[method]) index.set(`${method.toUpperCase()} ${key}`, item[method])
    }
  }
  return index
}

function deref(spec, schema) {
  let current = schema
  for (let hop = 0; current && current.$ref && hop < 10; hop += 1) {
    const name = current.$ref.split('/').pop()
    current = spec.components?.schemas?.[name]
  }
  return current ?? null
}

const refName = (schema) => (schema?.$ref ? schema.$ref.split('/').pop() : null)

function responseSchema(op) {
  for (const code of ['200', '201', '202']) {
    const schema = op.responses?.[code]?.content?.['application/json']?.schema
    if (schema) return schema
  }
  return null
}

function propsOf(spec, schema) {
  const resolved = deref(spec, schema)
  return resolved?.properties ? Object.keys(resolved.properties) : null
}

/* ---------------- 比对 ---------------- */
/** 返回 { problems, warnings }，元素为 { feName, text }（feName 用于 baseline 指纹）。 */
function diffFields(feName, feFields, beFields, modelName) {
  const beSet = new Set(beFields)
  const feSet = new Set(feFields)
  const extra = feFields.filter((f) => !beSet.has(f))
  const missing = beFields.filter((f) => !feSet.has(f))
  const problems = []
  const warnings = []
  if (extra.length) {
    problems.push({
      feName,
      text: `${feName} ← ${modelName}：前端多出 [${extra.join(', ')}]（后端不返回 → 运行时恒 undefined）`,
    })
  }
  if (missing.length) {
    warnings.push({
      feName,
      text: `${feName} ← ${modelName}：后端多出 [${missing.join(', ')}]（前端未声明，未使用）`,
    })
  }
  return { problems, warnings }
}

function analyze(spec, routes, interfaces, call) {
  const op = routes.get(`${call.method} ${call.path}`)
  if (!op) return { skip: 'OpenAPI 无该路由' }
  const schema = responseSchema(op)
  if (!schema) return { skip: '无 200/201 JSON 响应 schema' }

  /* 内联对象：`{ runs: X[]; total: number }` */
  const inline = call.typeExpr.match(/^\{(.+)\}$/s)
  if (inline) {
    const beProps = propsOf(spec, schema)
    if (!beProps) return { skip: '响应非对象（无 properties）' }
    const resolved = deref(spec, schema)
    const problems = []
    const warnings = []
    const checked = []
    for (const raw of inline[1].split(/[;\n]/)) {
      const fm = raw.trim().match(/^(\w+)\??\s*:\s*(.+?)$/)
      if (!fm) continue
      const [, key, valueExpr] = fm
      if (!beProps.includes(key)) {
        problems.push({
          feName: call.typeExpr,
          text: `内联对象 ← ${refName(schema) ?? '匿名'}：字段 ${key} 后端不返回`,
        })
        continue
      }
      const arrayOf = valueExpr.match(/^(\w+)\[\]$/)
      if (!arrayOf || !interfaces.has(arrayOf[1])) continue
      const itemsSchema = resolved?.properties?.[key]?.items
      const itemProps = propsOf(spec, itemsSchema)
      if (!itemProps) continue
      const result = diffFields(
        arrayOf[1],
        interfaces.get(arrayOf[1]),
        itemProps,
        refName(itemsSchema) ?? '数组元素',
      )
      problems.push(...result.problems)
      warnings.push(...result.warnings)
      checked.push(arrayOf[1])
    }
    return { problems, warnings, checked }
  }

  /* 数组：`X[]` */
  const arrayMatch = call.typeExpr.match(/^(\w+)\[\]$/)
  if (arrayMatch) {
    const resolved = deref(spec, schema)
    if (resolved?.type !== 'array') return { skip: '前端期望数组，后端非数组' }
    const feName = arrayMatch[1]
    if (!interfaces.has(feName)) return { skip: `types.ts 无 interface ${feName}` }
    const itemProps = propsOf(spec, resolved.items)
    if (!itemProps) return { skip: '数组元素无 properties' }
    const result = diffFields(
      feName,
      interfaces.get(feName),
      itemProps,
      refName(resolved.items) ?? '数组元素',
    )
    return { ...result, checked: [feName] }
  }

  /* 具名 interface */
  if (!interfaces.has(call.typeExpr)) return { skip: `types.ts 无 interface ${call.typeExpr}` }
  const beProps = propsOf(spec, schema)
  if (!beProps) return { skip: '响应非对象（无 properties）' }
  const result = diffFields(
    call.typeExpr,
    interfaces.get(call.typeExpr),
    beProps,
    refName(schema) ?? '匿名',
  )
  return { ...result, checked: [call.typeExpr] }
}

/* ---------------- 主流程 ---------------- */
const spec = await loadSpec()
const interfaces = parseInterfaces(await readFile(TYPES_FILE, 'utf8'))
const routes = buildRouteIndex(spec)
const calls = await parseCalls()

let baseline = []
try {
  const raw = JSON.parse(await readFile(BASELINE_FILE, 'utf8'))
  baseline = Array.isArray(raw?.known) ? raw.known : []
} catch {
  /* 无 baseline 文件 = 零容忍 */
}
const baselineSet = new Set(baseline)

const fresh = []
const known = []
const warnings = []
const skips = []
const checkedTypes = new Set()

for (const call of calls) {
  const where = `${call.method} ${call.path}`
  const result = analyze(spec, routes, interfaces, call)
  if (result.skip) {
    skips.push(`${where}（${call.file}）：${result.skip}`)
    continue
  }
  for (const item of result.problems ?? []) {
    const fingerprint = `${where} → ${item.feName}`
    if (baselineSet.has(fingerprint)) known.push(`${where} → ${item.text}`)
    else fresh.push(`${where} → ${item.text}`)
  }
  for (const item of result.warnings ?? []) warnings.push(`${where} → ${item.text}`)
  for (const name of result.checked ?? []) checkedTypes.add(name)
}

if (AS_JSON) {
  process.stdout.write(
    `${JSON.stringify(
      {
        calls: calls.length,
        checkedTypes: checkedTypes.size,
        fresh,
        known,
        warnings,
        skips,
        strict: STRICT,
      },
      null,
      2,
    )}\n`,
  )
} else {
  process.stdout.write(
    `[contract] 调用点 ${calls.length} 个 · 已比对 interface ${checkedTypes.size} 个 · ` +
      `新增漂移 ${fresh.length} · 已知漂移 ${known.length} · 告警 ${warnings.length} · 跳过 ${skips.length}\n`,
  )
  if (fresh.length) {
    process.stdout.write('\n✗ 新增字段漂移（前端声明了后端不返回的字段）：\n')
    for (const item of fresh) process.stdout.write(`  - ${item}\n`)
  }
  if (known.length) {
    process.stdout.write('\n· 已知漂移（在 contract-baseline.json 内，修完请删对应行）：\n')
    for (const item of known) process.stdout.write(`  - ${item}\n`)
  }
  if (warnings.length) {
    process.stdout.write('\n· 告警（后端多出字段，前端未声明）：\n')
    for (const item of warnings) process.stdout.write(`  - ${item}\n`)
  }
  if (skips.length) {
    process.stdout.write('\n· 跳过（未纳入比对）：\n')
    for (const item of skips) process.stdout.write(`  - ${item}\n`)
  }
}

/* 门禁失效保护：解析不到任何调用点 = 脚本自身坏了，不能静默通过 */
if (calls.length === 0) {
  process.stderr.write('[contract] 未解析到任何前端调用点 —— 门禁失效，按失败处理\n')
  process.exit(2)
}

const failed = fresh.length > 0 || (STRICT && warnings.length > 0)
if (!AS_JSON) process.stdout.write(failed ? '\n[contract] 失败\n' : '\n[contract] 通过\n')
process.exit(failed ? 1 : 0)
