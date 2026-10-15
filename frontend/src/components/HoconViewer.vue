<script setup lang="ts">
/**
 * M-P 前端控制台 — HOCON 代码块（轻量语法高亮，FR-55）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 7 章（页面 3）
 * 覆盖 FR：FR-55（HOCON 语法高亮）
 * 对应 AC：AC-10（端到端演示链路）
 *
 * 实现口径：不引入 highlight.js/prism（体积换来的收益有限，HOCON 语法极简），
 * 用**行级正则**给 key / 字符串 / 数字 / 注释上色——足够还原原型图的观感。
 * 高亮在**受控渲染**下进行（先 escape 再插标记），不执行任何用户输入。
 */
import { computed } from 'vue'

const props = defineProps<{ code: string | null | undefined; maxHeight?: string }>()

const ESCAPE_MAP: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>]/g, (char) => ESCAPE_MAP[char] ?? char)
}

/** 单行高亮：注释 → 键名 → 字符串 → 数字/布尔（顺序即优先级）。 */
function highlightLine(line: string): string {
  const escaped = escapeHtml(line)
  if (escaped.trimStart().startsWith('#')) {
    return `<span class="hk-comment">${escaped}</span>`
  }
  return escaped
    .replace(/^(\s*)([\w.-]+)(\s*[:=])/, '$1<span class="hk-key">$2</span>$3')
    .replace(/(&quot;|")([^"&]*)(&quot;|")/g, '<span class="hk-str">$1$2$3</span>')
    .replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="hk-num">$1</span>')
    .replace(/\b(true|false|null)\b/g, '<span class="hk-bool">$1</span>')
}

const html = computed(() => (props.code ?? '').split('\n').map(highlightLine).join('\n'))
</script>

<template>
  <!--
    v-html 是**受控渲染**：`highlightLine()` 先做 HTML 转义（& < >），再插入
    仅有的几个 <span class="hk-*"> 标记；用户输入不可能变成可执行标记。
    见 `escapeHtml` / `highlightLine` 实现与文件头注释。
  -->
  <!-- eslint-disable-next-line vue/no-v-html -->
  <pre class="code hocon" :style="{ maxHeight: maxHeight ?? '340px' }" v-html="html"></pre>
</template>

<style scoped>
.hocon {
  tab-size: 2;
}

.hocon :deep(.hk-key) {
  color: #1f56d6;
}
.hocon :deep(.hk-str) {
  color: #0f766e;
}
.hocon :deep(.hk-num) {
  color: #b45309;
}
.hocon :deep(.hk-bool) {
  color: #7c3aed;
}
.hocon :deep(.hk-comment) {
  color: #94a3b8;
  font-style: italic;
}
</style>
