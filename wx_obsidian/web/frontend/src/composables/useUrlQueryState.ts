/**
 * 把筛选/分页/视图状态同步到 URL query。
 *
 * 为什么必须做：现在筛选条件只活在组件内存里，刷新、后退、把链接发给别人都会丢。
 * 约定：空值不写进 URL（保持地址干净），默认值也不写。
 */

import { computed, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

type QueryState = Record<string, string>

export interface UseUrlQueryStateReturn<T extends QueryState> {
  state: Ref<T>
  /** 合并式更新（会清掉传空串的键） */
  patch: (partial: Partial<T>) => void
  reset: () => void
  isDefault: Ref<boolean>
}

function readFromRoute<T extends QueryState>(defaults: T, raw: Record<string, unknown>): T {
  const next = { ...defaults }
  Object.keys(defaults).forEach((key) => {
    const value = raw[key]
    const text = Array.isArray(value) ? String(value[0] ?? '') : value === undefined || value === null ? '' : String(value)
    next[key as keyof T] = (text || defaults[key]) as T[keyof T]
  })
  return next
}

export function useUrlQueryState<T extends QueryState>(defaults: T): UseUrlQueryStateReturn<T> {
  const route = useRoute()
  const router = useRouter()
  const state = ref(readFromRoute(defaults, route.query)) as Ref<T>

  const isDefault = computed(() =>
    Object.keys(defaults).every((key) => String(state.value[key] ?? '') === String(defaults[key] ?? '')),
  )

  function toQuery(value: T): Record<string, string> {
    const query: Record<string, string> = {}
    Object.keys(defaults).forEach((key) => {
      const current = String(value[key] ?? '')
      if (current !== '' && current !== String(defaults[key] ?? '')) {
        query[key] = current
      }
    })
    return query
  }

  function syncToRoute(value: T): void {
    const next = toQuery(value)
    const currentKeys = Object.keys(route.query)
    const same =
      currentKeys.length === Object.keys(next).length &&
      Object.entries(next).every(([key, item]) => String(route.query[key] ?? '') === item)
    if (same) {
      return
    }
    // replace 而不是 push：翻页/改筛选不该塞满浏览器历史（返回键要回到上个页面）
    void router.replace({ query: next })
  }

  function patch(partial: Partial<T>): void {
    state.value = { ...state.value, ...partial }
  }

  function reset(): void {
    state.value = { ...defaults }
  }

  // 本地 → URL
  watch(state, (value) => syncToRoute(value), { deep: true })
  // URL → 本地（浏览器前进/后退、外部带参进入）
  watch(
    () => route.query,
    (query) => {
      const incoming = readFromRoute(defaults, query)
      if (JSON.stringify(incoming) !== JSON.stringify(state.value)) {
        state.value = incoming
      }
    },
  )

  return { state, patch, reset, isDefault }
}
