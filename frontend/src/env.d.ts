/// <reference types="vite/client" />

/**
 * .vue 单文件组件的类型声明（让 TS 能 import *.vue）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P
 * 覆盖 FR：FR-53~58
 * 对应 AC：AC-10、AC-17
 */
declare module '*.vue' {
  import type { DefineComponent } from 'vue'

  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}
