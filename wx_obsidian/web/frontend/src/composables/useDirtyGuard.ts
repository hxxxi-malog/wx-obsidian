/**
 * 未保存改动的守卫（配置页用）。
 *
 * 行为：
 *  - `dirty` 为真时，路由离开弹确认；关闭标签页走浏览器原生提示。
 *  - 保存成功后调用 `markClean()` 清掉基线。
 * 现在的问题是新值静默丢失——用户在配置页改了 10 个字段，切个页面就没了。
 */

import { computed, onScopeDispose, ref, toRaw, watch, type Ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

export interface UseDirtyGuardReturn {
  dirty: Ref<boolean>
  markClean: () => void
  /** 需要自己接管确认流程时（例如保存并离开），先解锁 */
  bypass: () => void
}

export function useDirtyGuard(snapshot: () => unknown): UseDirtyGuardReturn {
  const baseline = ref(JSON.stringify(toRaw(snapshot())))
  const unlocked = ref(false)

  const dirty = computed(() => !unlocked.value && JSON.stringify(toRaw(snapshot())) !== baseline.value)

  function markClean(): void {
    baseline.value = JSON.stringify(toRaw(snapshot()))
    unlocked.value = false
  }

  function bypass(): void {
    unlocked.value = true
  }

  watch(dirty, (value) => {
    if (value) {
      window.addEventListener('beforeunload', onBeforeUnload)
    } else {
      window.removeEventListener('beforeunload', onBeforeUnload)
    }
  })

  function onBeforeUnload(event: BeforeUnloadEvent): void {
    event.preventDefault()
    // 现代浏览器忽略自定义文案，但必须设置 returnValue 才会弹窗
    event.returnValue = ''
  }

  onBeforeRouteLeave(() => {
    if (!dirty.value) {
      return true
    }
    return window.confirm('配置有未保存的改动，确定离开并丢弃吗？')
  })

  onScopeDispose(() => window.removeEventListener('beforeunload', onBeforeUnload))

  return { dirty, markClean, bypass }
}
