import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import FeedSyncDock from '@/components/layout/FeedSyncDock.vue'
import type { FeedSyncState } from '@/lib/api/types'
import { useFeedSyncStore } from '@/stores/feed-sync'

function sync(overrides: Partial<FeedSyncState> = {}): FeedSyncState {
  return {
    running: true,
    total: 29,
    triggered: 3,
    skipped: 0,
    failed: 0,
    started_at: '2026-10-23T17:00:00+08:00',
    finished_at: '',
    error: '',
    ...overrides,
  }
}

function mountDock() {
  setActivePinia(createPinia())
  const store = useFeedSyncStore()
  store.visible = true
  store.data = sync()
  const wrapper = mount(FeedSyncDock)
  return { wrapper, store }
}

describe('FeedSyncDock：右下角后台更新进度', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('不可见时不渲染任何内容', () => {
    const wrapper = mount(FeedSyncDock)
    expect(wrapper.find('[aria-label="公众号更新进度"]').exists()).toBe(false)
  })

  it('进行中显示计数进度，并说明不阻塞使用', () => {
    const { wrapper } = mountDock()
    const card = wrapper.get('[aria-label="公众号更新进度"]')
    expect(card.text()).toContain('正在触发公众号更新 3/29')
    expect(card.text()).toContain('可以继续使用其他页面')
    expect(card.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('10')
  })

  it('结束后显示触发结果，有跳过时如实说明', async () => {
    const { wrapper, store } = mountDock()
    store.data = sync({ running: false, triggered: 27, skipped: 2, failed: 0 })
    await wrapper.vm.$nextTick()
    const card = wrapper.get('[aria-label="公众号更新进度"]')
    expect(card.text()).toContain('公众号更新已触发')
    expect(card.text()).toContain('已触发 27 个公众号更新，2 个因过于频繁被跳过')
  })

  it('有失败时给出警告文案', async () => {
    const { wrapper, store } = mountDock()
    store.data = sync({ running: false, triggered: 20, failed: 9 })
    await wrapper.vm.$nextTick()
    const card = wrapper.get('[aria-label="公众号更新进度"]')
    expect(card.text()).toContain('部分公众号更新触发失败')
    expect(card.text()).toContain('9 个触发失败')
  })

  it('点关闭即收起进度卡', async () => {
    const { wrapper, store } = mountDock()
    await wrapper.get('[aria-label="关闭更新进度"]').trigger('click')
    expect(store.visible).toBe(false)
    expect(wrapper.find('[aria-label="公众号更新进度"]').exists()).toBe(false)
  })
})
