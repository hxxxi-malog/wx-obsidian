/**
 * 未保存改动的守卫（配置页用）。
 *
 * 行为：
 *  - `dirty` 为真时，路由离开弹确认；关闭标签页走浏览器原生提示。
 *  - 保存成功后调用 `markClean()` 清掉基线。
 * 现在的问题是新值静默丢失——用户在配置页改了 10 个字段，切个页面就没了。
 *
 * ⚠️ 实现要点：**不要对 snapshot() 的结果做 toRaw**。
 * `JSON.stringify(reactive 对象)` 会逐个属性读取，从而建立依赖，字段级改动能触发重算；
 * 一旦 toRaw 先把代理剥掉，后续读取不再经过代理，computed 永不重算（表现为「改了字段
 * 仍显示已保存、保存按钮永远禁用」）。`snapshot` 直接返回 reactive 对象即可，
 * 返回 `{ ...reactive }` 之类的浅拷贝同样有效（两者都会被正确追踪）。
 */

import { computed, onScopeDispose, ref, watch, type Ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

export interface UseDirtyGuardReturn {
  dirty: Ref<boolean>
  markClean: () => void
  /** 需要自己接管确认流程时（例如保存并离开），先解锁 */
  bypass: () => void
}

export function useDirtyGuard(snapshot: () => unknown): UseDirtyGuardReturn {
  const serialize = (): string => JSON.stringify(snapshot() ?? null)

  const baseline = ref(serialize())
  const unlocked = ref(false)

  const dirty = computed(() => !unlocked.value && serialize() !== baseline.value)

  function markClean(): void {
    baseline.value = serialize()
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
