<script setup lang="ts">
/**
 * M-P 前端控制台 — DAG 拓扑渲染（FR-55「DAG 渲染」）。
 *
 * SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 7 章（页面 3）
 * 覆盖 FR：FR-55（Pipeline Studio：DAG 拓扑图）
 * 对应 AC：AC-10（端到端演示链路）
 *
 * 实现口径：不引入图形库（graphviz/d3 会显著抬升前端体积且原型图为线性链），
 * 用**自上而下的节点链**渲染 Source → Transform(s) → Sink，节点间画连接线。
 * 节点来自后端 EtlPlan 的 `source / transform / sink` 三段结构。
 */
import { computed } from 'vue'

const props = defineProps<{
  plan: Record<string, unknown> | null | undefined
}>()

interface DagNode {
  kind: 'SOURCE' | 'TRANSFORM' | 'SINK'
  title: string
  detail: string
}

const nodes = computed<DagNode[]>(() => {
  const plan = props.plan
  if (!plan) {
    return []
  }
  const result: DagNode[] = []
  const source = plan.source as Record<string, unknown> | undefined
  const sink = plan.sink as Record<string, unknown> | undefined
  const transform = plan.transform

  if (source) {
    result.push({
      kind: 'SOURCE',
      title: String(source.connection ?? source.table ?? 'source'),
      detail: String(source.table ?? ''),
    })
  }
  if (transform) {
    const items = Array.isArray(transform) ? transform : [transform]
    items.forEach((item, index) => {
      if (typeof item === 'object' && item !== null) {
        const entry = item as Record<string, unknown>
        result.push({
          kind: 'TRANSFORM',
          title: String(entry.type ?? `transform-${index + 1}`),
          detail: String(entry.expr ?? entry.provider ?? ''),
        })
      }
    })
  }
  if (sink) {
    result.push({
      kind: 'SINK',
      title: String(sink.connection ?? sink.table ?? 'sink'),
      detail: String(sink.table ?? ''),
    })
  }
  return result
})
</script>

<template>
  <div class="dag">
    <template v-if="nodes.length">
      <template v-for="(node, index) in nodes" :key="index">
        <div class="dag__node" :class="`dag__node--${node.kind.toLowerCase()}`">
          <div class="dag__kind">{{ node.kind }}</div>
          <div class="dag__title">{{ node.title }}</div>
          <div v-if="node.detail" class="dag__detail">{{ node.detail }}</div>
        </div>
        <div v-if="index < nodes.length - 1" class="dag__link">
          <span class="dag__dot"></span>
          <span class="dag__line"></span>
          <span class="dag__dot"></span>
        </div>
      </template>
    </template>
    <div v-else class="dag__empty">暂无 EtlPlan（生成完成后出现 DAG）</div>
  </div>
</template>

<style scoped>
.dag {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0;
  padding: 8px 0;
}

.dag__node {
  width: 100%;
  max-width: 320px;
  border: 1px solid var(--color-border-strong);
  border-left-width: 3px;
  border-radius: var(--radius-sm);
  padding: 9px 12px;
  background: #fff;
}

.dag__node--source {
  border-left-color: var(--color-info);
}
.dag__node--transform {
  border-left-color: var(--color-warn);
}
.dag__node--sink {
  border-left-color: var(--color-success);
}

.dag__kind {
  font-size: 9.5px;
  letter-spacing: 0.06em;
  color: var(--color-text-muted);
}

.dag__title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text);
  font-family: var(--font-mono);
}

.dag__detail {
  font-size: 10.5px;
  color: var(--color-text-muted);
  font-family: var(--font-mono);
}

.dag__link {
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 26px;
}

.dag__dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--color-border-strong);
}

.dag__line {
  flex: 1;
  width: 1.5px;
  background: var(--color-border-strong);
}

.dag__empty {
  color: var(--color-text-muted);
  font-size: 12px;
  padding: 24px 0;
  text-align: center;
}
</style>
