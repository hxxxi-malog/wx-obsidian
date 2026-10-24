/**
 * 任务状态 store：SSE 实时进度 + 断线重连 + 刷新页面后重挂。
 *
 * 设计要点：
 *  1. 事件归约是**纯函数** `reduceJobEvent`，便于单测覆盖每种事件与重放去重。
 *  2. 终态（finished/cancelled/error）必须 close()，否则 EventSource 会不断重连。
 *  3. 事件带单调递增 seq：重连时后端会回放，重复帧按 seq 丢弃，避免计数与日志翻倍。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError, apiGet, apiPost, errorMessage, eventStreamUrl } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { JobEvent, JobList, JobResultItem, JobSnapshot, JobState } from '@/lib/api/types'
import { jobStageLabel } from '@/lib/status'

const TERMINAL_STATES: JobState[] = ['finished', 'error', 'cancelled']
const MAX_LOG_LINES = 200

export interface JobLogLine {
  seq: number
  ts: string
  level: string
  text: string
}

export interface JobsRuntimeState {
  job: JobSnapshot | null
  items: JobResultItem[]
  logLines: JobLogLine[]
  error: string
  /** 后端错误码（例如 job_busy），页面据此给精确提示；'' 表示没有结构化错误 */
  errorCode: string
  connected: boolean
  lastSeq: number
}

/** ApiError → { error, errorCode }：保留错误码，页面才能区分 409 单飞拒绝等分支 */
function describe(err: unknown): { error: string; errorCode: string } {
  return {
    error: errorMessage(err),
    errorCode: err instanceof ApiError ? err.code : '',
  }
}

function pushLine(state: JobsRuntimeState, text: string, level = 'INFO', ts = ''): JobLogLine[] {
  if (!text) {
    return state.logLines
  }
  const line: JobLogLine = { seq: state.lastSeq, ts: ts || new Date().toISOString(), level, text }
  const next = [...state.logLines, line]
  return next.length > MAX_LOG_LINES ? next.slice(next.length - MAX_LOG_LINES) : next
}

/** 事件 → 新状态（纯函数；不改入参） */
export function reduceJobEvent(state: JobsRuntimeState, event: JobEvent): JobsRuntimeState {
  if (typeof event.seq === 'number' && event.seq > 0 && event.seq <= state.lastSeq) {
    return state
  }
  const lastSeq = typeof event.seq === 'number' && event.seq > state.lastSeq ? event.seq : state.lastSeq

  let job = state.job
  if (job) {
    const nextJob: JobSnapshot = { ...job }
    if (typeof event.total === 'number' && event.total > 0) {
      nextJob.total = event.total
    }
    if (typeof event.completed === 'number') {
      nextJob.completed = event.completed
    }
    if (event.title) {
      nextJob.last_title = event.title
    }
    if (typeof event.stage === 'string' && event.state === 'begin') {
      nextJob.stage = event.stage
    } else if (event.state === 'end') {
      nextJob.stage = ''
    }
    job = nextJob
  }

  const base: JobsRuntimeState = { ...state, job, lastSeq }

  switch (event.type) {
    case 'start':
      return {
        ...base,
        logLines: pushLine(base, event.total ? `开始处理 ${event.total} 篇` : '任务已启动', 'INFO', event.ts),
      }
    case 'article':
      return {
        ...base,
        logLines: event.title
          ? pushLine(base, `[${event.completed ?? '?'}/${event.total ?? '?'}] ${event.title}`, 'INFO', event.ts)
          : base.logLines,
      }
    case 'stage':
      return {
        ...base,
        logLines: pushLine(
          base,
          event.stage === 'knowledge_graph' ? '正在更新知识图谱…' : '正在计算文章关联…',
          'INFO',
          event.ts,
        ),
      }
    case 'detail':
      return { ...base, logLines: pushLine(base, event.message ?? '', 'INFO', event.ts) }
    case 'empty':
      return {
        ...base,
        logLines: pushLine(base, `没有新文章需要处理（${event.detail ?? ''}）`, 'WARNING', event.ts),
      }
    case 'log':
      return {
        ...base,
        logLines: pushLine(base, event.message ?? '', (event.level ?? 'INFO').toUpperCase(), event.ts),
      }
    case 'finished':
    case 'cancelled':
    case 'error': {
      const result = event.result
      const terminal: JobState = event.type
      const nextJob = job ? { ...job, state: terminal, result: result ?? {}, finished_at: event.ts } : job
      if (event.type === 'error') {
        const message = event.error || '任务失败'
        return {
          ...base,
          job: nextJob,
          items: result?.items ?? base.items,
          error: message,
          connected: false,
          logLines: pushLine(base, `任务失败：${message}`, 'ERROR', event.ts),
        }
      }
      if (event.type === 'cancelled') {
        return {
          ...base,
          job: nextJob,
          items: result?.items ?? base.items,
          connected: false,
          logLines: pushLine(base, `任务已取消（完成 ${result?.done ?? 0} 篇）`, 'WARNING', event.ts),
        }
      }
      return {
        ...base,
        job: nextJob,
        items: result?.items ?? base.items,
        connected: false,
        logLines: pushLine(
          base,
          `任务完成：成功 ${result?.done ?? 0} 篇，失败 ${result?.failed ?? 0} 篇`,
          'INFO',
          event.ts,
        ),
      }
    }
    default:
      return base
  }
}

export function initialJobsState(): JobsRuntimeState {
  return { job: null, items: [], logLines: [], error: '', errorCode: '', connected: false, lastSeq: 0 }
}

export const useJobsStore = defineStore('jobs', () => {
  const state = ref<JobsRuntimeState>(initialJobsState())
  const recent = ref<JobSnapshot[]>([])
  const now = ref(Date.now())
  let source: EventSource | null = null
  let ticker: number | null = null

  const job = computed(() => state.value.job)
  const items = computed(() => state.value.items)
  const logLines = computed(() => state.value.logLines)
  const error = computed(() => state.value.error)
  const errorCode = computed(() => state.value.errorCode)
  const connected = computed(() => state.value.connected)
  const running = computed(() => state.value.job?.state === 'running')
  const stageLabel = computed(() => jobStageLabel(state.value.job?.stage))
  const percent = computed(() => {
    const current = state.value.job
    if (!current || !current.total) {
      return 0
    }
    return Math.min(100, Math.round((current.completed / current.total) * 100))
  })
  const summary = computed(() => state.value.job?.result ?? {})
  const failedItems = computed(() => state.value.items.filter((item) => item.status !== 'done'))

  /** 已运行时长（毫秒）：跑动中由 ticker 推着走，结束就定格 */
  const elapsedMs = computed(() => {
    const current = state.value.job
    if (!current?.started_at) {
      return 0
    }
    const start = new Date(current.started_at).getTime()
    const end = current.finished_at ? new Date(current.finished_at).getTime() : now.value
    return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : 0
  })

  function startTicker(): void {
    if (ticker !== null) {
      return
    }
    now.value = Date.now()
    ticker = window.setInterval(() => {
      now.value = Date.now()
      if (!running.value) {
        stopTicker()
      }
    }, 1000)
  }

  function stopTicker(): void {
    if (ticker !== null) {
      window.clearInterval(ticker)
      ticker = null
    }
  }

  function detach(): void {
    if (source) {
      source.close()
      source = null
    }
    state.value = { ...state.value, connected: false }
    stopTicker()
  }

  function apply(event: JobEvent): void {
    const next = reduceJobEvent(state.value, event)
    state.value = next
    if (!next.connected || TERMINAL_STATES.includes(next.job?.state as JobState)) {
      if (next.job && TERMINAL_STATES.includes(next.job.state)) {
        detach()
      }
    } else {
      startTicker()
    }
  }

  function attach(jobId: string): void {
    detach()
    const stream = new EventSource(eventStreamUrl(endpoints.jobs.events(jobId)))
    source = stream
    stream.onopen = () => {
      state.value = { ...state.value, connected: true }
      startTicker()
    }
    stream.addEventListener('job', (raw) => {
      try {
        apply(JSON.parse((raw as MessageEvent).data) as JobEvent)
      } catch {
        // 忽略坏帧，不影响后续事件
      }
    })
    stream.onerror = () => {
      state.value = { ...state.value, connected: false }
      // 任务已结束时的「错误」只是流关闭，不必打扰用户
      if (state.value.job && TERMINAL_STATES.includes(state.value.job.state)) {
        detach()
      }
    }
  }

  /** 刷新页面/切换路由后重新挂上正在跑的任务；返回值表示本次读取是否成功 */
  async function refresh(): Promise<boolean> {
    try {
      const data = await apiGet<JobList>(endpoints.jobs.list())
      recent.value = data.recent
      const current = data.current
      // 读取成功就清掉上一次的错误：否则失败一次之后，页面上的错误块与
      // 「刷新成功」的提示会长期打架
      state.value = { ...state.value, job: current, error: '', errorCode: '' }
      if (current) {
        if (!source || source.readyState === EventSource.CLOSED) {
          attach(current.job_id)
        }
      } else {
        detach()
      }
      if (!current && data.recent.length) {
        state.value = { ...state.value, items: data.recent[0].result?.items ?? [] }
      }
      return true
    } catch (err) {
      state.value = { ...state.value, ...describe(err) }
      return false
    }
  }

  async function startFetch(params: {
    limit?: number
    force?: boolean
    articleId?: string
    articleIds?: string[]
  }): Promise<boolean> {
    state.value = { ...initialJobsState() }
    try {
      const payload: Record<string, unknown> = { kind: params.articleIds ? 'retry' : 'fetch' }
      if (params.limit) payload.limit = params.limit
      if (params.force) payload.force = true
      if (params.articleId) payload.article_id = params.articleId
      if (params.articleIds) payload.article_ids = params.articleIds

      const response = await apiPost<{ job_id: string }>(endpoints.jobs.create(), payload)
      const optimistic: JobSnapshot = {
        job_id: response.job_id,
        kind: String(payload.kind),
        state: 'running',
        started_at: new Date().toISOString(),
        finished_at: null,
        completed: 0,
        total: params.limit ?? 0,
        last_title: '',
        stage: '',
        result: {},
        error: '',
      }
      state.value = { ...state.value, job: optimistic, error: '', errorCode: '' }
      attach(response.job_id)
      return true
    } catch (err) {
      state.value = { ...state.value, ...describe(err) }
      return false
    }
  }

  async function cancel(): Promise<void> {
    const current = state.value.job
    if (!current) {
      return
    }
    try {
      await apiPost(endpoints.jobs.cancel(current.job_id))
      state.value = {
        ...state.value,
        logLines: pushLine(state.value, '已请求取消…', 'WARNING'),
      }
    } catch (err) {
      state.value = { ...state.value, ...describe(err) }
    }
  }

  function clearLog(): void {
    state.value = { ...state.value, logLines: [] }
  }

  function clearError(): void {
    state.value = { ...state.value, error: '', errorCode: '' }
  }

  return {
    job,
    items,
    failedItems,
    logLines,
    error,
    errorCode,
    connected,
    running,
    percent,
    summary,
    stageLabel,
    elapsedMs,
    recent,
    refresh,
    startFetch,
    cancel,
    clearLog,
    clearError,
    detach,
  }
})
