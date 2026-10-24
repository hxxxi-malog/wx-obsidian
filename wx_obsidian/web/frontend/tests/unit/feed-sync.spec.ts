import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { feedSyncPercent, feedSyncView } from '@/lib/feed-sync'
import type { FeedSyncState } from '@/lib/api/types'

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/client')>()
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn() }
})

import { apiGet, apiPost } from '@/lib/api/client'
import { useFeedSyncStore } from '@/stores/feed-sync'

const mockedGet = vi.mocked(apiGet)
const mockedPost = vi.mocked(apiPost)

function sync(overrides: Partial<FeedSyncState> = {}): FeedSyncState {
  return {
    running: false,
    total: 29,
    triggered: 29,
    skipped: 0,
    failed: 0,
    started_at: '2026-10-23T17:00:00+08:00',
    finished_at: '2026-10-23T17:01:30+08:00',
    error: '',
    ...overrides,
  }
}

describe('feedSyncView：右下角进度卡的文案与状态', () => {
  it('还没拿到数据时显示「正在触发」', () => {
    const view = feedSyncView(null)
    expect(view.running).toBe(true)
    expect(view.title).toContain('正在触发公众号更新')
    expect(view.percent).toBe(0)
  })

  it('进行中按「已处理数 / 总数」算百分比', () => {
    const view = feedSyncView(sync({ running: true, total: 29, triggered: 3, skipped: 1, failed: 1 }))
    expect(view.running).toBe(true)
    expect(view.title).toBe('正在触发公众号更新 5/29')
    expect(view.percent).toBe(17)
    expect(view.detail).toContain('可以继续使用其他页面')
  })

  it('全部成功时给完成态', () => {
    const view = feedSyncView(sync())
    expect(view.running).toBe(false)
    expect(view.tone).toBe('success')
    expect(view.percent).toBe(100)
    expect(view.detail).toContain('已触发 29 个公众号更新')
  })

  it('有失败时给警告态并列出跳过数量', () => {
    const view = feedSyncView(sync({ triggered: 25, skipped: 2, failed: 2 }))
    expect(view.tone).toBe('warning')
    expect(view.title).toContain('部分公众号更新触发失败')
    expect(view.detail).toContain('2 个因过于频繁被跳过')
    expect(view.detail).toContain('2 个触发失败')
  })

  it('没有订阅时如实说明', () => {
    const view = feedSyncView(sync({ total: 0, triggered: 0 }))
    expect(view.detail).toContain('当前没有订阅的公众号')
  })

  it('轮询出错时也按警告态展示', () => {
    const view = feedSyncView(sync({ running: false }), 'Network Error')
    expect(view.tone).toBe('warning')
    expect(view.detail).toContain('Network Error')
  })

  it('百分比被夹在 0..100', () => {
    expect(feedSyncPercent(null)).toBe(0)
    expect(feedSyncPercent(sync({ total: 0 }))).toBe(0)
    expect(feedSyncPercent(sync({ total: 2, triggered: 5, skipped: 0, failed: 0 }))).toBe(100)
  })
})

describe('feed-sync store：后台轮询与自动收起', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    mockedGet.mockReset()
    mockedPost.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('start 立刻显示进度卡，结束后自动收起', async () => {
    mockedGet
      .mockResolvedValueOnce(sync({ running: true, total: 29, triggered: 3 }))
      .mockResolvedValueOnce(sync({ running: false }))

    const store = useFeedSyncStore()
    store.start()
    expect(store.visible).toBe(true)

    await vi.advanceTimersByTimeAsync(0)
    expect(store.data?.running).toBe(true)
    expect(store.data?.triggered).toBe(3)

    await vi.advanceTimersByTimeAsync(1500)
    expect(store.data?.running).toBe(false)

    // 结果停留 8 秒后自动收起
    await vi.advanceTimersByTimeAsync(8000)
    expect(store.visible).toBe(false)
  })

  it('手动关闭后停止轮询', async () => {
    mockedGet.mockResolvedValue(sync({ running: true, total: 29, triggered: 1 }))

    const store = useFeedSyncStore()
    store.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(mockedGet).toHaveBeenCalledTimes(1)

    store.dismiss()
    expect(store.visible).toBe(false)

    await vi.advanceTimersByTimeAsync(6000)
    expect(mockedGet).toHaveBeenCalledTimes(1)
  })

  it('刷新页面后能接回仍在跑的同步', async () => {
    mockedGet.mockResolvedValue(sync({ running: true, total: 29, triggered: 7 }))

    const store = useFeedSyncStore()
    await store.resume()

    expect(store.visible).toBe(true)
    expect(store.data?.triggered).toBe(7)
    store.dismiss()
  })

  it('没有在跑的同步时不显示进度卡', async () => {
    mockedGet.mockResolvedValue(sync({ running: false }))

    const store = useFeedSyncStore()
    await store.resume()

    expect(store.visible).toBe(false)
  })

  it('轮询失败时记下错误并收起', async () => {
    mockedGet.mockRejectedValueOnce(new Error('网络断了'))

    const store = useFeedSyncStore()
    store.start()
    await vi.advanceTimersByTimeAsync(0)

    expect(store.error).toContain('网络断了')
    await vi.advanceTimersByTimeAsync(16000)
    expect(store.visible).toBe(false)
  })

  it('trigger 先提交手动采集，再轮询到结束并发出完成标记', async () => {
    mockedPost.mockResolvedValueOnce(sync({ running: true, total: 29, triggered: 1 }))
    mockedGet
      .mockResolvedValueOnce(sync({ running: true, total: 29, triggered: 12 }))
      .mockResolvedValueOnce(sync({ running: false, total: 29, triggered: 29 }))

    const store = useFeedSyncStore()
    expect(store.finishedTick).toBe(0)

    const ok = await store.trigger()
    expect(ok).toBe(true)
    expect(mockedPost).toHaveBeenCalledTimes(1)
    expect(store.data?.triggered).toBe(1)

    await vi.advanceTimersByTimeAsync(1500)
    expect(store.data?.triggered).toBe(12)

    await vi.advanceTimersByTimeAsync(1500)
    expect(store.data?.running).toBe(false)
    expect(store.finishedTick).toBe(1)

    store.dismiss()
  })

  it('trigger 请求失败时返回 false，进度卡按错误态收起', async () => {
    mockedPost.mockRejectedValueOnce(new Error('网络断了'))

    const store = useFeedSyncStore()
    const ok = await store.trigger()

    expect(ok).toBe(false)
    expect(store.error).toContain('网络断了')
    await vi.advanceTimersByTimeAsync(16000)
    expect(store.visible).toBe(false)
  })
})
