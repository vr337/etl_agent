/**
 * M-P 前端控制台 — 路由表（5 大视图 + 版本 Diff + 工作区初始化）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 7 章（页面原型）
 * 覆盖 FR：FR-53（总览）/ FR-54（数据源与资产）/ FR-55（Studio + Diff）/
 *   FR-56（运行中心与审批）/ FR-57（安全治理与 Benchmark）/ FR-58（初始化与身份注入）
 * 对应 AC：AC-10（端到端演示链路）、AC-17（版本 Diff 视图）
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/bootstrap',
    name: 'bootstrap',
    component: () => import('@/views/Bootstrap.vue'),
    // 尚无工作区时左导航目标都不存在，故全屏布局（App.vue 依据 meta.layout 分流）
    meta: { layout: 'fullscreen', title: '工作区初始化' },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { title: '总览工作台', subtitle: '控制面治理事实与执行态势总览' },
  },
  {
    path: '/datasources',
    name: 'datasources',
    component: () => import('@/views/DataSources.vue'),
    meta: { title: '数据源与资产', subtitle: '数据连接 · 凭据治理 · 元数据探查 · 文件资产' },
  },
  {
    path: '/studio',
    name: 'studio',
    component: () => import('@/views/PipelineStudio.vue'),
    meta: {
      title: 'Pipeline Studio',
      subtitle: '自然语言需求 · 澄清中断恢复 · 确定性门禁 · 不可变版本与 Diff',
    },
  },
  {
    path: '/runtime',
    name: 'runtime',
    component: () => import('@/views/RuntimeCenter.vue'),
    meta: { title: '运行中心与审批控制台', subtitle: '执行监控 · 运行监督 · 四眼审批 · 单次 Capability' },
  },
  {
    path: '/safety',
    name: 'safety',
    component: () => import('@/views/SafetyGovernance.vue'),
    meta: { title: '安全治理与 Benchmark', subtitle: 'PDP 策略包 · 证据账本 · L0/L1/L2 评测 · 受控发布' },
  },
  {
    path: '/versions/diff',
    name: 'version-diff',
    component: () => import('@/views/VersionDiff.vue'),
    meta: {
      title: '不可变版本 Diff',
      subtitle: 'Schema / HOCON / 质量规则差异 · 版本只能派生、不可原地覆盖',
    },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard',
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
