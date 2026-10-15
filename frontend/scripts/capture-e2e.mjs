/**
 * S10 真实联调验证脚本（fix/S10-e2e-integration）。
 *
 * 与 `capture-screens.mjs` 的区别：
 *   - 那个脚本只做「UI 还原对照」，容忍接口失败、预置假身份；
 *   - 本脚本做**真实前后端联调**——走完整 Bootstrap 流程，采集每个 /api/v1
 *     请求的 HTTP 状态，断言**无 401 / 无 405**，再把页面截到 S10-e2e/。
 *
 * 用途：把「联调通过」落成可核验证据（截图 + 网络状态清单），
 * 供 `docs/demo/演示回放.md` 引用。
 *
 * 前置：后端 :8000 与前端 :5173 均已启动，且 BOOTSTRAP_TOKEN 与 infra/.env 一致。
 *
 * 用法：node scripts/capture-e2e.mjs
 */
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const args = process.argv.slice(2)
const argOf = (name, fallback) => {
  const index = args.indexOf(name)
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback
}

const BASE = argOf('--base', 'http://127.0.0.1:5173')
const REPO_ROOT = path.resolve(__dirname, '..', '..')
const OUT = path.resolve(REPO_ROOT, argOf('--out', path.join('docs', 'stages', 'assets', 'S10-e2e')))

const BOOTSTRAP_TOKEN = argOf('--token', process.env.S10_BOOTSTRAP_TOKEN ?? '')
const ADMIN_SUBJECT = argOf('--subject', 'admin@demo.local')

/** 联调走过的页面（Bootstrap 之后）。 */
const PAGES = [
  { route: '/dashboard', name: 'E1-dashboard' },
  { route: '/runtime', name: 'E2-runtime-center' },
  { route: '/datasources', name: 'E3-datasources' },
  { route: '/studio', name: 'E4-pipeline-studio' },
  { route: '/safety', name: 'E5-safety-governance' },
  { route: '/versions/diff', name: 'E6-version-diff' },
]

/** 需要重点核查的端点（联调修复目标）。 */
const WATCHED = [
  '/api/v1/projects/',  // executions / preparations 列表都在此前缀下
]

async function main() {
  if (!BOOTSTRAP_TOKEN) {
    console.error('缺少 Bootstrap Token：用 --token <值> 或环境变量 S10_BOOTSTRAP_TOKEN 传入')
    process.exit(2)
  }
  await mkdir(OUT, { recursive: true })

  const browser = await chromium.launch()
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1200 },
    deviceScaleFactor: 1,
    locale: 'zh-CN',
  })

  // 清空历史身份，确保走真实 Bootstrap（而非沿用旧 localStorage）
  const seed = await context.newPage()
  await seed.goto(BASE, { waitUntil: 'domcontentloaded' }).catch(() => {})
  await seed.evaluate(() => {
    localStorage.removeItem('actor_id')
    localStorage.removeItem('project_id')
  })
  await seed.close()

  const networkLog = []
  const page = await context.newPage()

  page.on('response', async (response) => {
    const url = response.url()
    if (!url.includes('/api/v1')) return
    const method = response.request().method()
    const status = response.status()
    const apiPath = url.replace(BASE, '')
    networkLog.push({ method, status, path: apiPath })
  })

  // ---- 步骤 1：真实 Bootstrap ----
  console.log('① Bootstrap（真实流程）')
  await page.goto(`${BASE}/bootstrap`, { waitUntil: 'networkidle', timeout: 20000 })
  await page.waitForTimeout(600)

  await page.fill('input[placeholder="演示工作区"]', '演示工作区')
  await page.fill('input[placeholder="admin@demo.local"]', ADMIN_SUBJECT)
  await page.fill('input[placeholder*="BOOTSTRAP_TOKEN"]', BOOTSTRAP_TOKEN)
  await page.click('button:has-text("初始化工作区")')
  await page.waitForTimeout(1500)

  await page.screenshot({ path: path.join(OUT, 'E0-bootstrap-done.png'), fullPage: true })
  console.log('   → E0-bootstrap-done.png')

  const stored = await page.evaluate(() => ({
    actor_id: localStorage.getItem('actor_id'),
    project_id: localStorage.getItem('project_id'),
  }))
  console.log(`   localStorage.actor_id = ${stored.actor_id}`)
  console.log(`   localStorage.project_id = ${stored.project_id}`)
  const actorIsSubject = stored.actor_id === ADMIN_SUBJECT

  // 进入控制台
  const enter = page.locator('button:has-text("进入控制台")')
  if (await enter.count()) {
    await enter.click()
    await page.waitForTimeout(1500)
  }

  // ---- 步骤 2：逐页访问 + 截图 ----
  console.log('② 逐页联调')
  for (const view of PAGES) {
    await page.goto(`${BASE}${view.route}`, { waitUntil: 'networkidle', timeout: 20000 }).catch(() => {})
    await page.waitForTimeout(1400)
    await page.screenshot({ path: path.join(OUT, `${view.name}.png`), fullPage: true })
    console.log(`   → ${view.name}.png`)
  }

  await browser.close()

  // ---- 步骤 3：网络状态盘点 ----
  const bad = networkLog.filter((entry) => entry.status === 401 || entry.status === 405)
  const watched = networkLog.filter((entry) => WATCHED.some((prefix) => entry.path.startsWith(prefix)))
  const summary = {
    bootstrap: { actorIsSubject, stored },
    totalApiRequests: networkLog.length,
    statusCounts: networkLog.reduce((acc, entry) => {
      acc[entry.status] = (acc[entry.status] ?? 0) + 1
      return acc
    }, {}),
    authOrMethodErrors: bad,
    projectScopedRequests: watched,
  }

  await writeFile(path.join(OUT, 'e2e-network.json'), JSON.stringify(summary, null, 2), 'utf-8')

  console.log('')
  console.log(`API 请求总数：${networkLog.length}`)
  console.log('状态码分布：', JSON.stringify(summary.statusCounts))
  console.log(`401/405 命中：${bad.length}`)
  console.log(`actor_id 是否为 subject：${actorIsSubject ? '是 ✓' : '否 ✗'}`)
  console.log(`产物目录：${OUT}`)

  if (bad.length) {
    console.error('联调未通过：存在 401 / 405 请求')
    console.error(JSON.stringify(bad, null, 2))
    process.exit(1)
  }
  if (!actorIsSubject) {
    console.error('联调未通过：localStorage.actor_id 不是 subject（Bug 1 未修复）')
    process.exit(1)
  }
  console.log('联调通过：无 401 / 无 405，actor 为 subject ✓')
}

main().catch((error) => {
  console.error('联调脚本失败：', error)
  process.exit(1)
})
