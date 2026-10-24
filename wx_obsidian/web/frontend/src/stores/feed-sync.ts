/**
 * 登录后「触发公众号更新」的全局进度 store。
 *
 * 为什么放在 store 而不是账号页：扫码成功后对话框立刻关闭，用户会接着去
 * 「公众号管理」刷新；同步进度必须跨路由继续轮询，右下角的进度卡才不会被
 * 页面卸载带走。后端本来就是后台线程在跑，这里只做轮询与展示。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { apiGet, apiPost, errorMessage } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { FeedSyncState } from '@/lib/api/types'

/** 进度轮询间隔（后端每触发一个公众号约 3 秒） */
const POLL_INTERVAL_MS = 1500
/** 结束后结果卡停留时间，让用户看清数字；有失败时停留更久 */
const RESULT_LINGER_MS = 8000

export const useFeedSyncStore = defineStore('feed-sync', () => {
  const data = ref<FeedSyncState | null>(null)
  const visible = ref(false)
  const error = ref('')
  const busy = ref(false)
  /** 每观察到一轮采集从「进行中」变为「已结束」就加一，供页面触发列表刷新 */
  const finishedTick = ref(0)

  let pollTimer: ReturnType<typeof setInterval> | null = null
  let lingerTimer: ReturnType<typeof setTimeout> | null = null

  const running = computed(() => Boolean(data.value?.running))

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function clearLinger(): void {
    if (lingerTimer !== null) {
      clearTimeout(lingerTimer)
      lingerTimer = null
    }
  }

  function scheduleDismiss(): void {
    clearLinger()
    const delay = data.value?.failed || error.value ? RESULT_LINGER_MS * 2 : RESULT_LINGER_MS
    lingerTimer = setTimeout(() => {
      visible.value = false
      lingerTimer = null
    }, delay)
  }

  /** 取一次进度；结束后停轮询并安排自动收起 */
  async function refresh(): Promise<void> {
    if (busy.value) {
      return
    }
    busy.value = true
    const wasRunning = Boolean(data.value?.running)
    try {
      const next = await apiGet<FeedSyncState>(endpoints.source.sync())
      data.value = next
      if (next.error) {
        error.value = next.error
      }
      if (!next.running) {
        stopPolling()
        scheduleDismiss()
        if (wasRunning) {
          finishedTick.value += 1
        }
      }
    } catch (err) {
      stopPolling()
      error.value = errorMessage(err)
      scheduleDismiss()
    } finally {
      busy.value = false
    }
  }

  /** 扫码登录成功后调用：立刻显示进度卡并开始轮询 */
  function start(): void {
    clearLinger()
    stopPolling()
    error.value = ''
    data.value = null
    visible.value = true
    void refresh()
    pollTimer = setInterval(() => void refresh(), POLL_INTERVAL_MS)
  }

  /**
   * 手动触发一轮采集（「立即采集」按钮）：
   * POST 拿到当前快照后与 login 流程共用同一套轮询与进度卡。
   *
   * Returns:
   * 已成功提交（或本来就在跑）返回 true；请求失败返回 false。
   */
  async function trigger(): Promise<boolean> {
    clearLinger()
    stopPolling()
    error.value = ''
    data.value = null
    visible.value = true
    try {
      const next = await apiPost<FeedSyncState>(endpoints.source.triggerSync())
      data.value = next
      if (next.error) {
        error.value = next.error
      }
    } catch (err) {
      error.value = errorMessage(err)
      scheduleDismiss()
      return false
    }
    if (!data.value?.running) {
      scheduleDismiss()
      finishedTick.value += 1
      return true
    }
    pollTimer = setInterval(() => void refresh(), POLL_INTERVAL_MS)
    return true
  }

  /** 刷新页面后把仍在跑的后台同步挂回来（没有在跑就什么都不做） */
  async function resume(): Promise<void> {
    if (visible.value) {
      return
    }
    try {
      const next = await apiGet<FeedSyncState>(endpoints.source.sync())
      if (!next.running) {
        return
      }
      data.value = next
      visible.value = true
      stopPolling()
      pollTimer = setInterval(() => void refresh(), POLL_INTERVAL_MS)
    } catch {
      // 数据源不可达时账号页的探针会给出明确提示，这里保持安静
    }
  }

  function dismiss(): void {
    stopPolling()
    clearLinger()
    visible.value = false
  }

  return {
    data,
    visible,
    error,
    running,
    finishedTick,
    start,
    trigger,
    resume,
    dismiss,
    refresh,
  }
})
