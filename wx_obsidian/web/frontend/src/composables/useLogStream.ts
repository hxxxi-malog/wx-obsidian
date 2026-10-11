/**
 * 系统日志事件流（GET /api/logs/events，SSE）。
 *
 * 与旧的 3s 轮询相比：实时、且不再周期性打断服务。
 * 断线由 EventSource 自动重连，重连时浏览器会带 Last-Event-ID，后端据此回放，
 * 因此本地再去一次重（seq 单调）即可保证不重复、不丢行。
 */

import { useDocumentVisibility } from '@vueuse/core'
import { onScopeDispose, ref, watch, type Ref } from 'vue'

import { eventStreamUrl } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { LogEntry } from '@/lib/api/types'

const MAX_ENTRIES = 300

export interface UseLogStreamReturn {
  entries: Ref<LogEntry[]>
  connected: Ref<boolean>
  /** 手动暂停（用户点「暂停」时不再新增长行，但不断流） */
  paused: Ref<boolean>
  start: () => void
  stop: () => void
  clear: () => void
  togglePause: () => void
}

export function useLogStream(options: { max?: number } = {}): UseLogStreamReturn {
  const max = options.max ?? MAX_ENTRIES
  const entries = ref<LogEntry[]>([])
  const connected = ref(false)
  const paused = ref(false)
  const visibility = useDocumentVisibility()

  let source: EventSource | null = null
  let lastSeq = 0

  function append(entry: LogEntry): void {
    if (!entry || typeof entry.seq !== 'number' || entry.seq <= lastSeq) {
      return
    }
    lastSeq = entry.seq
    if (paused.value) {
      return
    }
    const next = [...entries.value, entry]
    entries.value = next.length > max ? next.slice(next.length - max) : next
  }

  function stop(): void {
    if (source) {
      source.close()
      source = null
    }
    connected.value = false
  }

  function start(): void {
    if (source || visibility.value === 'hidden') {
      return
    }
    const stream = new EventSource(eventStreamUrl(endpoints.logs.events(lastSeq)))
    source = stream
    stream.onopen = () => {
      connected.value = true
    }
    stream.onerror = () => {
      // SSE 的正常结束/重连都会走到这里；EventSource 会自己重连，不弹错
      connected.value = false
    }
    stream.addEventListener('log', (raw) => {
      try {
        append(JSON.parse((raw as MessageEvent).data) as LogEntry)
      } catch {
        // 坏帧跳过，不影响后续
      }
    })
  }

  function clear(): void {
    entries.value = []
  }

  function togglePause(): void {
    paused.value = !paused.value
  }

  watch(visibility, (state) => {
    if (state === 'hidden') {
      stop()
    } else {
      start()
    }
  })

  onScopeDispose(stop)

  return { entries, connected, paused, start, stop, clear, togglePause }
}
