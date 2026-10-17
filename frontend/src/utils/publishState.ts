/**
 * 发布状态派生（T-OBS-01）——**镜像后端** `execution/publish.py::current_publish_state`。
 *
 * 背景：`execution_runs` 没有发布状态列（SPEC 5.2 未定义，改契约需 AMEND），状态由
 * 事件流的 `execution.published` / `execution.publish_blocked` 承载；后端只在
 * `publish_run` 内部用它做幂等判断，**从不对外暴露**。结果是运维要看「这个运行发布
 * 成没成 / 为什么被阻断」，只能自己翻 `GET /executions/{id}` 的 `events[]`——而前端
 * 页面压根没渲染事件流，等于这个状态没有可视化渠道。
 *
 * 本模块让前端**用同一份已返回的数据**派生它，因此：
 * - 不动后端、不加接口字段、不改契约（不需要 AMEND）；
 * - 不影响 `check:contract` 门禁（`ExecutionDetailView` 本就含 `events[]`）。
 *
 * 口径与后端逐条对齐（后端：PUBLISHED > BLOCKED > READY，均以事件流为事实源）：
 * | 后端常量 | 前端值 |
 * | --- | --- |
 * | `STATE_PUBLISHED` | `PUBLISHED` |
 * | `STATE_BLOCKED` | `BLOCKED` |
 * | `STATE_READY` | `READY` |
 */

export type PublishState = 'PUBLISHED' | 'BLOCKED' | 'READY'

export interface PublishStateView {
  state: PublishState
  /** BLOCKED 时给出后端留痕的原因（`execution.publish_blocked` 的 message）；其余为 null */
  reason: string | null
}

/** 事件类型常量（与后端 `publish.py` 同值；此处不反向依赖 Python）。 */
const EVENT_PUBLISHED = 'execution.published'
const EVENT_BLOCKED = 'execution.publish_blocked'

/**
 * 从执行详情的事件流派生发布状态。
 *
 * @param events `GET /executions/{id}` 的 `events[]`（只读 event_type / message，不解析 payload）
 */
export function derivePublishState(
  events: { event_type: string; message: string | null }[] | undefined | null,
): PublishStateView {
  const list = events ?? []
  if (list.some((item) => item.event_type === EVENT_PUBLISHED)) {
    return { state: 'PUBLISHED', reason: null }
  }
  const blocked = list.find((item) => item.event_type === EVENT_BLOCKED)
  if (blocked) {
    return { state: 'BLOCKED', reason: blocked.message ?? null }
  }
  return { state: 'READY', reason: null }
}
