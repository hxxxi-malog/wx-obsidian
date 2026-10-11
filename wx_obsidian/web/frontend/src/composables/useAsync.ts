/**
 * 统一的一次性异步取数：三态（loading/error/data）+ 卸载时中断。
 *
 * 约定：每个数据视图都必须同时实现 loading（骨架）、error（可重试）、empty（有引导）。
 * 用这个 composable 就不会漏 —— 三个 ref 都在返回值里。
 */

import { onScopeDispose, ref, shallowRef, type Ref } from 'vue'

import { errorMessage, isAbort } from '@/lib/api/client'

export interface UseAsyncOptions<T> {
  /** 初次装载的占位值（默认 null） */
  initial?: T | null
  /** 出错时是否清空旧数据（默认保留，避免刷新失败把页面变空） */
  clearOnError?: boolean
  /** 传 true 时立即执行一次 */
  immediate?: boolean
}

export interface UseAsyncReturn<T> {
  data: Ref<T | null>
  loading: Ref<boolean>
  error: Ref<string>
  /** 至少成功跑过一次（用于区分「首次加载」与「刷新中」） */
  loaded: Ref<boolean>
  run: () => Promise<T | null>
  reset: () => void
}

export function useAsync<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  options: UseAsyncOptions<T> = {},
): UseAsyncReturn<T> {
  const data = shallowRef<T | null>(options.initial ?? null) as Ref<T | null>
  const loading = ref(false)
  const error = ref('')
  const loaded = ref(false)
  let controller: AbortController | null = null
  let disposed = false

  onScopeDispose(() => {
    disposed = true
    controller?.abort()
  })

  async function run(): Promise<T | null> {
    controller?.abort()
    const current = new AbortController()
    controller = current
    loading.value = true
    error.value = ''
    try {
      const result = await loader(current.signal)
      if (disposed || current.signal.aborted) {
        return null
      }
      data.value = result
      loaded.value = true
      return result
    } catch (err) {
      if (isAbort(err) || disposed) {
        return null
      }
      error.value = errorMessage(err)
      if (options.clearOnError) {
        data.value = options.initial ?? null
      }
      return null
    } finally {
      if (controller === current) {
        loading.value = false
      }
    }
  }

  function reset(): void {
    controller?.abort()
    data.value = options.initial ?? null
    loading.value = false
    error.value = ''
    loaded.value = false
  }

  if (options.immediate) {
    void run()
  }

  return { data, loading, error, loaded, run, reset }
}
