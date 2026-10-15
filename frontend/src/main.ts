/**
 * M-P 前端控制台 — SPA 入口（挂载 Pinia + Router + 全局样式）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P
 * 覆盖 FR：FR-53~58
 * 对应 AC：AC-10（端到端演示链路）、AC-17（版本 Diff 视图）
 */
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import './assets/styles/main.css'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
