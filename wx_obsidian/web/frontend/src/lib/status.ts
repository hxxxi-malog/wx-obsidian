/** 领域状态映射：一处定义，全站复用，避免每个页面各写一份 Record。 */

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info'

export interface StatusMeta {
  label: string
  tone: Tone
}

/** 文章处理状态（processed.json 的 status 字段） */
export const ARTICLE_STATUS: Record<string, StatusMeta> = {
  done: { label: '已处理', tone: 'success' },
  error: { label: '失败', tone: 'danger' },
  failed: { label: '失败', tone: 'danger' },
  skipped: { label: '跳过', tone: 'warning' },
  cancelled: { label: '已取消', tone: 'info' },
  pending: { label: '待处理', tone: 'neutral' },
}

export function articleStatusMeta(status: string): StatusMeta {
  return ARTICLE_STATUS[status] ?? { label: status || '未知', tone: 'neutral' }
}

/** 任务（JobSnapshot.state） */
export const JOB_STATE: Record<string, StatusMeta> = {
  running: { label: '运行中', tone: 'accent' },
  finished: { label: '已完成', tone: 'success' },
  cancelled: { label: '已取消', tone: 'info' },
  error: { label: '失败', tone: 'danger' },
}

export function jobStateMeta(state: string | null | undefined): StatusMeta {
  if (!state) {
    return { label: '空闲', tone: 'neutral' }
  }
  return JOB_STATE[state] ?? { label: state, tone: 'neutral' }
}

/** 任务阶段哨兵 → 中文（后端 _start/_kg/_related 的语义） */
export const JOB_STAGE_LABEL: Record<string, string> = {
  knowledge_graph: '更新知识图谱',
  related: '计算文章关联',
  fetch: '抓取文章',
  summarize: '生成笔记',
}

export function jobStageLabel(stage: string | null | undefined): string {
  if (!stage) {
    return ''
  }
  return JOB_STAGE_LABEL[stage] ?? stage
}

/**
 * 抓取流程的阶段时间线（顺序即展示顺序）。
 * 只列后端**真的会发** stage 事件的阶段（`related` / `knowledge_graph`），
 * 「处理文章」是每一篇 article 事件所在的默认阶段——不编造后端没有的阶段名。
 */
export const JOB_STAGE_TIMELINE: { key: string; label: string }[] = [
  { key: 'articles', label: '逐篇处理' },
  { key: 'related', label: '计算关联' },
  { key: 'knowledge_graph', label: '更新知识图谱' },
]

/** 当前阶段在时间线里的下标 */
export function jobStageIndex(stage: string | null | undefined): number {
  if (stage === 'related') {
    return 1
  }
  if (stage === 'knowledge_graph') {
    return 2
  }
  return 0
}

/** 日志级别 → 色调（页面告警面板只收 WARNING 以上） */
export const LOG_LEVEL_TONE: Record<string, Tone> = {
  CRITICAL: 'danger',
  ERROR: 'danger',
  WARNING: 'warning',
  INFO: 'info',
  DEBUG: 'neutral',
}

export function logLevelTone(level: string): Tone {
  return LOG_LEVEL_TONE[level?.toUpperCase()] ?? 'neutral'
}

/** 连通性探测结果 */
export function connectionTone(success: boolean | undefined, configured = true): Tone {
  if (!configured) {
    return 'neutral'
  }
  return success ? 'success' : 'danger'
}
