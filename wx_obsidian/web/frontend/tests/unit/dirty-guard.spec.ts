import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, nextTick, reactive } from 'vue'
import { describe, expect, it } from 'vitest'

import { useDirtyGuard } from '@/composables/useDirtyGuard'

/**
 * 回归测试：脏值守卫必须能感知**字段级**改动。
 *
 * 曾经的 bug：实现里对 snapshot() 的结果做了 `toRaw`，代理被剥掉后读取不再建立依赖，
 * computed 永不重算——表现是「改了字段仍显示已保存、保存按钮永远禁用」。
 * 这个用例锁住「直接传 reactive 对象」这种自然用法。
 */
interface Harness {
  form: { name: string; port: number }
  guard: ReturnType<typeof useDirtyGuard>
}

async function mountGuard(): Promise<Harness> {
  const form = reactive({ name: 'wx', port: 8765 })
  let guard!: ReturnType<typeof useDirtyGuard>

  const Page = defineComponent({
    setup() {
      guard = useDirtyGuard(() => form)
      return () => null
    },
  })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: Page }],
  })
  await router.push('/')
  await router.isReady()

  mount(defineComponent({ template: '<router-view />' }), { global: { plugins: [router] } })
  await nextTick()
  return { form, guard }
}

describe('useDirtyGuard', () => {
  it('初始为干净', async () => {
    const { guard } = await mountGuard()
    expect(guard.dirty.value).toBe(false)
  })

  it('改动 reactive 对象的单个字段就变脏（依赖追踪不能断）', async () => {
    const { form, guard } = await mountGuard()
    form.port = 9000
    await nextTick()
    expect(guard.dirty.value).toBe(true)
  })

  it('markClean 后回到干净，再改又变脏', async () => {
    const { form, guard } = await mountGuard()
    form.name = 'changed'
    await nextTick()
    expect(guard.dirty.value).toBe(true)

    guard.markClean()
    await nextTick()
    expect(guard.dirty.value).toBe(false)

    form.name = 'again'
    await nextTick()
    expect(guard.dirty.value).toBe(true)
  })

  it('改回原值算干净（按内容比对，不是按「是否编辑过」）', async () => {
    const { form, guard } = await mountGuard()
    form.port = 1234
    await nextTick()
    expect(guard.dirty.value).toBe(true)
    form.port = 8765
    await nextTick()
    expect(guard.dirty.value).toBe(false)
  })

  it('bypass 后视为干净（用于「保存并离开」这类自己接管确认的流程）', async () => {
    const { form, guard } = await mountGuard()
    form.port = 7777
    await nextTick()
    guard.bypass()
    await nextTick()
    expect(guard.dirty.value).toBe(false)
  })
})
