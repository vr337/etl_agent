/**
 * S10 阶段 — UI 还原对照截图脚本（DoD：5 大视图逐一对照原型图）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 7 章（页面原型）
 * 覆盖 FR：FR-53~58
 * 对应 AC：AC-10（端到端演示链路）、AC-17（版本 Diff 视图）
 *
 * 用途：把 7 个视图逐一截图到 docs/stages/assets/S10/，供阶段报告的
 *   「UI 还原对照清单」引用。**不是测试**——即便后端未启动也要能出图，
 *   故页面内的接口失败被容忍（页面自身有保底演示态）。
 *
 * 为什么要有这一步：S10 提示词把「UI 还原」列为强制项，
 *   而「对照」需要可核验的凭据——截图就是凭据。没有截图的「已还原」是口头的。
 *
 * 用法（dev server 需已在 5173 运行）：
 *   node scripts/capture-screens.mjs
 *   node scripts/capture-screens.mjs --base http://127.0.0.1:5173 --out ../docs/stages/assets/S10
 */
import { mkdir } from 'node:fs/promises'
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
// 默认落到**仓库根**的 docs/stages/assets/S10（而不是 frontend/ 下）：
// 阶段报告与原型图都在仓库根的 docs/，截图必须与它们同处一处才好对照。
// 故从 `frontend/scripts/` 上溯两级到仓库根。
const REPO_ROOT = path.resolve(__dirname, '..', '..')
const OUT = path.resolve(REPO_ROOT, argOf('--out', path.join('docs', 'stages', 'assets', 'S10')))

/** 视图 → 原型图对照（原型图位于 docs/prototype/images/）。 */
const VIEWS = [
  { route: '/dashboard', name: 'S1-dashboard', prototype: 'S1-dashboard.png', title: '总览工作台' },
  { route: '/datasources', name: 'S2-datasources', prototype: 'S2-datasources.png', title: '数据源与资产' },
  { route: '/studio', name: 'S3-pipeline-studio', prototype: 'S3-pipeline-studio.png', title: 'Pipeline Studio' },
  { route: '/runtime', name: 'S4-runtime-center', prototype: 'S4-runtime-center.png', title: '运行中心与审批' },
  { route: '/safety', name: 'S5-safety-governance', prototype: 'S5-safety-governance.png', title: '安全治理与评测' },
  { route: '/versions/diff', name: 'S6-version-diff', prototype: '(AC-17 独立视图)', title: '不可变版本 Diff' },
  { route: '/bootstrap', name: 'S7-bootstrap', prototype: '(FR-58 初始化)', title: '工作区初始化' },
]

/**
 * 预置身份：控制台视图在无 actor/project 时也会渲染（页面有保底演示态），
 * 但预置身份能让真实数据分支被走到，截图更接近原型图。
 */
const PRIMED_STORAGE = {
  actor_id: '11111111-1111-1111-1111-111111111111',
  project_id: '22222222-2222-2222-2222-222222222222',
}

async function main() {
  await mkdir(OUT, { recursive: true })

  const browser = await chromium.launch()
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1200 },
    deviceScaleFactor: 1,
    locale: 'zh-CN',
  })

  // 先访问同源页面，才能写 localStorage
  const seed = await context.newPage()
  await seed.goto(BASE, { waitUntil: 'domcontentloaded' }).catch(() => {})
  await seed.evaluate((store) => {
    for (const [key, value] of Object.entries(store)) {
      localStorage.setItem(key, value)
    }
  }, PRIMED_STORAGE)
  await seed.close()

  const results = []
  for (const view of VIEWS) {
    const page = await context.newPage()
    const errors = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', (error) => errors.push(String(error)))

    try {
      await page.goto(`${BASE}${view.route}`, { waitUntil: 'networkidle', timeout: 20000 })
    } catch {
      // 后端未起时 networkidle 可能超时；退回 domcontentloaded 也要出图
      await page.goto(`${BASE}${view.route}`, { waitUntil: 'domcontentloaded' }).catch(() => {})
    }

    // 等待骨架渲染（Vue 挂载 + 首屏数据回落）
    await page.waitForTimeout(1200)

    const file = path.join(OUT, `${view.name}.png`)
    await page.screenshot({ path: file, fullPage: true })

    const heading = await page.locator('h1').first().textContent().catch(() => null)
    results.push({ ...view, file, heading: (heading ?? '').trim(), errors })
    console.log(`  ${view.name.padEnd(22)} → ${path.relative(process.cwd(), file)}`)
    await page.close()
  }

  await browser.close()

  console.log('')
  console.log('视图 → 原型图对照：')
  for (const r of results) {
    const errNote = r.errors.length ? ` [console errors: ${r.errors.length}]` : ''
    console.log(`  ${r.route.padEnd(16)} 原型 ${r.prototype.padEnd(28)} 标题「${r.heading}」${errNote}`)
  }
  console.log('')
  console.log(`共 ${results.length} 张截图 → ${OUT}`)
}

main().catch((error) => {
  console.error('截图失败：', error)
  process.exit(1)
})
