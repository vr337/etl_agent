<script setup lang="ts">
/**
 * M-P 前端控制台 — 应用外壳（左导航 + 顶栏 + 通知栈 + 主内容插槽）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 7 章（页面原型）
 * 覆盖 FR：FR-53~58（5 张原型图共用的左导航与顶栏框架）
 * 对应 AC：AC-10（端到端演示链路）
 *
 * 还原口径：5 张原型图的左侧栏与顶栏**完全一致**，故抽为唯一外壳：
 * - 左侧栏（196px，深色 #131a2b）：logo 区 → 5 项导航 → 底部版本/网关注记；
 * - 顶栏（64px，白底）：页面标题 + 副标题（路由 meta）｜`prod` 徽标 + 操作者。
 *
 * 一处按裁决偏离原型图：底部版本注记原型图写 `SPEC v1.0.2-Baseline`，
 * 人类裁决改为 **v1.0.3**（「以原型图为准」仅适用于布局/组件，不适用于元数据文字）。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useActorStore } from '@/stores/actor'
import { useNotificationStore } from '@/stores/notification'

const route = useRoute()
const router = useRouter()
const actor = useActorStore()
const notifications = useNotificationStore()

const NAV_ITEMS = [
  { name: 'dashboard', label: '总览工作台', icon: 'grid' },
  { name: 'datasources', label: '数据源与资产', icon: 'database' },
  { name: 'studio', label: 'Pipeline Studio', icon: 'branch' },
  { name: 'runtime', label: '运行与审批', icon: 'pulse' },
  { name: 'safety', label: '安全治理与评测', icon: 'shield' },
] as const

const title = computed(() => (route.meta.title as string) ?? '')
const subtitle = computed(() => (route.meta.subtitle as string) ?? '')

function isActive(name: string): boolean {
  if (name === 'studio') {
    return route.name === 'studio' || route.name === 'version-diff'
  }
  return route.name === name
}

function goBootstrap() {
  router.push({ name: 'bootstrap' })
}
</script>

<template>
  <div class="shell">
    <!-- 左导航（原型图：深色竖栏 + 5 项 + 底部版本注记） -->
    <aside class="shell__nav">
      <div class="nav__brand">
        <div class="nav__logo">A</div>
        <div>
          <div class="nav__brand-name">ETL Agent</div>
          <div class="nav__brand-sub">治理型 ETL 平台</div>
        </div>
      </div>

      <div class="nav__section">控制台</div>

      <nav class="nav__list">
        <RouterLink
          v-for="item in NAV_ITEMS"
          :key="item.name"
          :to="{ name: item.name }"
          class="nav__item"
          :class="{ 'nav__item--active': isActive(item.name) }"
        >
          <span class="nav__icon" :data-icon="item.icon" aria-hidden="true"></span>
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="nav__foot">
        <div>SPEC v1.0.3-Baseline</div>
        <div class="muted">trusted-gateway</div>
      </div>
    </aside>

    <!-- 顶栏 + 主内容 -->
    <div class="shell__main">
      <header class="shell__topbar">
        <div>
          <h1 class="topbar__title">{{ title }}</h1>
          <p class="topbar__subtitle">{{ subtitle }}</p>
        </div>
        <div class="topbar__right">
          <span class="badge badge--info">prod</span>
          <button class="actor" type="button" @click="goBootstrap">
            <span class="actor__avatar">{{ actor.actorName.slice(0, 1).toUpperCase() }}</span>
            <span class="actor__meta">
              <span class="actor__name">{{ actor.actorName }}</span>
              <span class="actor__role">{{ actor.actorRole || '未绑定职责' }}</span>
            </span>
          </button>
        </div>
      </header>

      <main class="shell__content">
        <slot />
      </main>
    </div>

    <!-- 通知栈（SPEC 6.4 错误码 + trace_id） -->
    <div class="toasts">
      <div
        v-for="item in notifications.items"
        :key="item.id"
        class="toast"
        :class="`toast--${item.level}`"
        @click="notifications.dismiss(item.id)"
      >
        {{ item.message }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}

/* ---- 左导航 ---- */
.shell__nav {
  width: var(--sidebar-width);
  flex: 0 0 var(--sidebar-width);
  background: var(--color-nav-bg);
  display: flex;
  flex-direction: column;
  padding: 18px 12px;
  position: sticky;
  top: 0;
  height: 100vh;
}

.nav__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 6px 20px;
}

.nav__logo {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: var(--color-primary);
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 15px;
}

.nav__brand-name {
  color: #fff;
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.3;
}

.nav__brand-sub {
  color: var(--color-nav-text);
  font-size: 10.5px;
}

.nav__section {
  color: #5b647a;
  font-size: 10.5px;
  letter-spacing: 0.04em;
  padding: 0 10px 8px;
}

.nav__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav__item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 10px;
  border-radius: var(--radius-sm);
  color: var(--color-nav-text);
  font-size: 12.5px;
  font-weight: 500;
  transition: background 0.15s, color 0.15s;
}

.nav__item:hover {
  background: rgba(255, 255, 255, 0.05);
  color: #cbd3e1;
}

.nav__item--active {
  background: var(--color-nav-bg-active);
  color: var(--color-nav-text-active);
}

.nav__item--active:hover {
  background: var(--color-nav-bg-active);
  color: #fff;
}

/* 图标用纯 CSS 方块占位（原型图为线性图标；不引入图标库依赖） */
.nav__icon {
  width: 14px;
  height: 14px;
  border-radius: 3px;
  border: 1.5px solid currentColor;
  opacity: 0.85;
  flex: 0 0 14px;
}

.nav__icon[data-icon='grid'] {
  box-shadow: 6px 6px 0 -3.5px currentColor;
}
.nav__icon[data-icon='database'] {
  border-radius: 50%;
}
.nav__icon[data-icon='branch'] {
  border-style: dashed;
}
.nav__icon[data-icon='pulse'] {
  border-radius: 2px 8px 2px 8px;
}
.nav__icon[data-icon='shield'] {
  border-radius: 50% 50% 50% 2px;
}

.nav__foot {
  margin-top: auto;
  padding: 12px 10px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  color: #5b647a;
  font-size: 10px;
  line-height: 1.7;
}

/* ---- 主区 ---- */
.shell__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.shell__topbar {
  height: var(--topbar-height);
  flex: 0 0 var(--topbar-height);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  position: sticky;
  top: 0;
  z-index: 10;
}

.topbar__title {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
  color: var(--color-text);
}

.topbar__subtitle {
  font-size: 11px;
  color: var(--color-text-muted);
  margin: 1px 0 0;
}

.topbar__right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.actor {
  display: flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: var(--radius-sm);
}

.actor:hover {
  background: #f7f9fc;
}

.actor__avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  display: grid;
  place-items: center;
  font-weight: 600;
  font-size: 12.5px;
}

.actor__meta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.35;
}

.actor__name {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text);
}

.actor__role {
  font-size: 10px;
  color: var(--color-text-muted);
  letter-spacing: 0.02em;
}

.shell__content {
  flex: 1;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ---- 通知栈 ---- */
.toasts {
  position: fixed;
  right: 20px;
  bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 100;
  max-width: 380px;
}

.toast {
  padding: 10px 14px;
  border-radius: var(--radius-md);
  font-size: 12px;
  cursor: pointer;
  box-shadow: var(--shadow-pop);
  background: #fff;
  border-left: 3px solid var(--color-neutral);
}

.toast--error {
  border-left-color: var(--color-danger);
  color: #7f1d1d;
}
.toast--warn {
  border-left-color: var(--color-warn);
  color: #78350f;
}
.toast--success {
  border-left-color: var(--color-success);
  color: #14532d;
}
.toast--info {
  border-left-color: var(--color-info);
  color: #1e3a8a;
}
</style>
