<script setup lang="ts">
/**
 * M-P 前端控制台 — 根组件（按路由分流：Bootstrap 全屏引导 / 其余走 AppShell）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P
 * 覆盖 FR：FR-53~58（含 FR-58 工作区初始化引导）
 * 对应 AC：AC-10（端到端演示链路）、AC-17（版本 Diff 视图）
 *
 * 设计决策：Bootstrap 是「尚无工作区」时的入口，此时左导航的任何目标都不存在，
 * 故单独走全屏布局；一旦初始化完成（或既有 actor_id）即回到带外壳的正常视图。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppShell from '@/components/AppShell.vue'

const route = useRoute()
const isFullscreen = computed(() => route.meta.layout === 'fullscreen')
</script>

<template>
  <!--
    分流：Bootstrap 全屏（尚无工作区时左导航目标都不存在）／其余走 AppShell。
    注意：AppShell 是**外壳 + 插槽**结构——页面内容必须作为默认插槽传入
    （`<AppShell><RouterView /></AppShell>`）。只写 `<AppShell />` 会渲染出
    空壳：左导航与顶栏都在，但主内容区永远空白。
  -->
  <AppShell v-if="!isFullscreen">
    <RouterView />
  </AppShell>
  <RouterView v-else />
</template>
