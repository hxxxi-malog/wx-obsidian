/**
 * 任务状态 store：SSE 实时进度 + 断线重连 + 刷新页面后重挂。
 *
 * 关键细节：任务结束（事件流正常关闭）后必须 close()，否则 EventSource 会不断重连。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { apiGet, apiPost, buildQuery, errorMessage, eventStreamUrl } from '../api/client'
import type { JobEvent, JobList, JobResultItem, JobSnapshot } from '../api/types'

const TERMINAL_STATES = ['finished', 'error', 'cancelled']
const MAX_LOG_LINES = 200

export const useJobsStore = defineStore('jobs', () => {
  const job = ref<JobSnapshot | null>(null)
  const items = ref<JobResultItem[]>([])
  const logLines = ref<string[]>([])
  const error = ref('')
  const connected = ref(false)

  let source: EventSource | null = null

  const running = computed(() => job.value?.state === 'running')
  const percent = computed(() => {
    const current = job.value
    if (!current || !current.total) {
      return 0
    }
    return Math.min(100, Math.round((current.completed / current.total) * 100))
  })
  const summary = computed(() => job.value?.result ?? {})

  function pushLog(text: string): void {
    const stamp = new Date().toLocaleTimeString('zh-CN')
    logLines.value = [...logLines.value.slice(-(MAX_LOG_LINES - 1)), `[${stamp}] ${text}`]
  }

  function detach(): void {
    if (source) {
      source.close()
      source = null
    }
    connected.value = false
  }

  function applyEvent(event: JobEvent): void {
    const current = job.value
    if (current) {
      if (typeof event.total === 'number' && event.total > 0) {
        current.total = event.total
      }
      if (typeof event.completed === 'number') {
        current.completed = event.completed
      }
      if (event.title) {
        current.last_title = event.title
      }
      if (typeof event.stage === 'string' && event.state === 'begin') {
        current.stage = event.stage
      } else if (event.state === 'end') {
        current.stage = ''
      }
    }

    switch (event.type) {
      case 'start':
        pushLog(event.total ? `开始处理 ${event.total} 篇` : '任务已启动')
        break
      case 'article':
        if (event.title) {
          pushLog(`[${event.completed ?? '?'}/${event.total ?? '?'}] ${event.title}`)
        }
        break
      case 'stage':
        pushLog(event.stage === 'knowledge_graph' ? '正在更新知识图谱…' : '正在计算文章关联…')
        break
      case 'detail':
        pushLog(event.message ?? '')
        break
      case 'empty':
        pushLog(`没有新文章需要处理（${event.detail ?? ''}）`)
        break
      case 'log':
        pushLog(event.message ?? '')
        break
      case 'finished':
      case 'cancelled':
      case 'error': {
        const result = event.result
        if (result?.items) {
          items.value = result.items
        }
        if (event.type === 'error') {
          error.value = event.error || '任务失败'
          pushLog(`任务失败：${error.value}`)
        } else if (event.type === 'cancelled') {
          pushLog(`任务已取消（完成 ${result?.done ?? 0} 篇）`)
        } else {
          pushLog(`任务完成：成功 ${result?.done ?? 0} 篇，失败 ${result?.failed ?? 0} 篇`)
        }
        if (job.value) {
          job.value.state = event.type
          job.value.result = result ?? {}
        }
        detach()
        break
      }
      default:
        break
    }
  }

  function attach(jobId: string): void {
    detach()
    const stream = new EventSource(eventStreamUrl(`/api/jobs/${jobId}/events`))
    source = stream

    stream.onopen = () => {
      connected.value = true
    }
    stream.addEventListener('job', (raw) => {
      try {
        applyEvent(JSON.parse((raw as MessageEvent).data) as JobEvent)
      } catch {
        // 忽略坏帧，不影响后续事件
      }
    })
    stream.onerror = () => {
      connected.value = false
      // 任务已结束时的“错误”只是流关闭，不必打扰用户
      if (job.value && TERMINAL_STATES.includes(job.value.state)) {
        detach()
      }
    }
  }

  /** 刷新页面/切换路由后重新挂上正在跑的任务。 */
  async function refresh(): Promise<void> {
    try {
      const data = await apiGet<JobList>('/api/jobs')
      const current = data.current
      job.value = current
      if (current) {
        if (!source || source.readyState === EventSource.CLOSED) {
          attach(current.job_id)
        }
      } else {
        detach()
      }
      if (!current && data.recent.length) {
        const last = data.recent[0]
        items.value = last.result?.items ?? []
      }
    } catch (err) {
      error.value = errorMessage(err)
    }
  }

  async function startFetch(params: {
    limit?: number
    force?: boolean
    articleId?: string
    articleIds?: string[]
  }): Promise<void> {
    error.value = ''
    logLines.value = []
    items.value = []
    try {
      const payload: Record<string, unknown> = { kind: params.articleIds ? 'retry' : 'fetch' }
      if (params.limit) payload.limit = params.limit
      if (params.force) payload.force = true
      if (params.articleId) payload.article_id = params.articleId
      if (params.articleIds) payload.article_ids = params.articleIds

      const response = await apiPost<{ job_id: string }>('/api/jobs', payload)
      job.value = {
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
      attach(response.job_id)
    } catch (err) {
      error.value = errorMessage(err)
    }
  }

  async function cancel(): Promise<void> {
    const current = job.value
    if (!current) {
      return
    }
    try {
      await apiPost(`/api/jobs/${current.job_id}/cancel`)
      pushLog('已请求取消…')
    } catch (err) {
      error.value = errorMessage(err)
    }
  }

  async function refreshRecent(): Promise<JobSnapshot[]> {
    const data = await apiGet<JobList>(`/api/jobs${buildQuery({})}`)
    return data.recent
  }

  return {
    job,
    items,
    logLines,
    error,
    connected,
    running,
    percent,
    summary,
    refresh,
    startFetch,
    cancel,
    refreshRecent,
    detach,
  }
})
