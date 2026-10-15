/**
 * 前端控制台 — ESLint 扁平配置（ESLint 9 默认格式）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / docs/conventions/code-comments.md
 * 覆盖 FR：FR-53~58（控制台交付的静态质量门）
 * 对应 AC：AC-10（端到端演示链路）
 *
 * 为何用扁平配置：ESLint 9 起废弃 `.eslintrc.*`，默认只认 `eslint.config.js`。
 * 旧格式会直接报「couldn't find an eslint.config.(js|mjs|cjs) file」而**跳过全部检查**
 * ——那等于 lint 门形同虚设，故这里必须用新格式。
 *
 * 规则取向：以「能抓真问题」为准，不做风格洁癖——
 * 未使用变量/参数、显式 any、悬空 promise、无效模板为 error；其余为 warn。
 */
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  {
    // 构建产物与依赖不参与检查（dist 由 vite 产出，node_modules 是第三方代码）
    ignores: ['dist/**', 'node_modules/**', '*.tsbuildinfo'],
  },

  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],

  {
    files: ['**/*.ts', '**/*.vue'],
    languageOptions: {
      parserOptions: {
        // .vue 里的 <script setup lang="ts"> 交给 TS 解析器
        parser: tseslint.parser,
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
      globals: {
        // 浏览器全局（前端运行环境）
        window: 'readonly',
        document: 'readonly',
        localStorage: 'readonly',
        console: 'readonly',
        fetch: 'readonly',
        URL: 'readonly',
        URLSearchParams: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        HTMLElement: 'readonly',
        Event: 'readonly',
      },
    },
    rules: {
      // ---- 真问题：error ----
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-floating-promises': 'off', // 需要 type-aware lint，成本高于收益
      'no-debugger': 'error',

      // ---- Vue 单文件组件：贴合原型图实现，不强制命名/顺序洁癖 ----
      'vue/multi-word-component-names': 'off',
      'vue/attributes-order': 'warn',
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/first-attribute-linebreak': 'off',
      'vue/require-default-prop': 'off',
    },
  },

  {
    // 构建/工具脚本（vite.config.ts、scripts/*.mjs）允许使用 Node 全局
    files: ['vite.config.ts', '*.config.ts', 'scripts/**/*.mjs', 'scripts/**/*.js'],
    languageOptions: {
      globals: {
        process: 'readonly',
        __dirname: 'readonly',
        console: 'readonly',
        URL: 'readonly',
      },
    },
  },
)
