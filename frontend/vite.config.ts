/**
 * 前端控制台 — Vite 构建配置。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 附录 A.1
 * 覆盖 FR：FR-53~58（五大视图 + 工作区初始化与身份注入）
 * 对应 AC：AC-10（端到端演示链路）、AC-17（版本 Diff 视图）
 *
 * 关键决策：dev 下把 `/api` 代理到后端 :8000——同源代理让浏览器不触发 CORS，
 * 也让前端代码里的 baseURL 在 dev 与「后端同源托管 dist」两种形态下保持一致。
 */
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
