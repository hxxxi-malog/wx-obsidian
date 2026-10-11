/**
 * 轮询（页面隐藏时暂停）。
 *
 * 系统日志与仪表盘都要周期刷新，但切到后台标签页还照跑只是白烧 CPU 和连接数——
 * 可见性一变就停/起，并在恢复可见时立刻补一次。
 */

import { useDocumentVisibility, useIntervalFn } from '@vueuse/core'
import { onScopeDispose, ref, watch, type Ref } from 'vue'

export interface UsePollingReturn {
  active: Ref<boolean>
  start: () => void
  stop: () => void
  /** 立即执行一次（不重置计时） */
  trigger: () => Promise<void>
}

export function usePolling(
  task: () => unknown | Promise<unknown>,
  intervalMs: number,
  options: { immediate?: boolean } = {},
): UsePollingReturn {
  const active = ref(false)
  const visibility = useDocumentVisibility()

  async function trigger(): Promise<void> {
    try {
      await task()
    } catch {
      // 轮询失败不该打扰用户：页面上另有错误态负责呈现
    }
  }

  const { pause, resume } = useIntervalFn(trigger, intervalMs, { immediate: false })

  function start(): void {
    if (active.value) {
      return
    }
    active.value = true
    resume()
  }

  function stop(): void {
    if (!active.value) {
      return
    }
    active.value = false
    pause()
  }

  watch(visibility, (state) => {
    if (state === 'hidden') {
      stop()
    } else {
      start()
      void trigger()
    }
  })

  onScopeDispose(stop)

  if (options.immediate) {
    start()
    void trigger()
  }

  return { active, start, stop, trigger }
}
