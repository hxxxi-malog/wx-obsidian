/**
 * 手动刷新按钮的反馈契约：
 *  1. 读取成功 → 弹「刷新成功」，并在说明里带上这次读到的真实状态或数量；
 *  2. 请求本身成功、底层状态不正常（数据源不可达 / 令牌未配置）→ 弹警告，不谎报成功；
 *  3. 自动轮询走的是另一条入口，不得因为本契约每次都弹提示。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import type { ConfigSnapshot, FeedItem, JobList, SourceHealth } from '@/lib/api/types'
import { useJobsStore } from '@/stores/jobs'
import { useUiStore } from '@/stores/ui'
import Feeds from '@/views/Feeds.vue'
import Source from '@/views/Source.vue'

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/client')>()
  return { ...actual, apiGet: vi.fn() }
})

import { apiGet } from '@/lib/api/client'

const mockedGet = vi.mocked(apiGet)

function feed(overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id: 'feed-1',
    name: '某个公众号',
    intro: '',
    cover: '',
    sync_time: '2026-10-24T12:00:00+08:00',
    update_time: '2026-10-24T12:00:00+08:00',
    ...overrides,
  }
}

function config(token: string): ConfigSnapshot {
  return { source: { token } } as unknown as ConfigSnapshot
}

function health(overrides: Partial<SourceHealth> = {}): SourceHealth {
  return {
    healthy: true,
    type: 'we-mp-rss',
    base_url: 'http://localhost:8001',
    docker_hint: '',
    ...overrides,
  }
}

function testRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/settings', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ],
  })
}

function mountView(component: Parameters<typeof mount>[0]): {
  wrapper: VueWrapper
  ui: ReturnType<typeof useUiStore>
} {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(component, { global: { plugins: [pinia, testRouter()] } })
  return { wrapper, ui: useUiStore() }
}

/** 点页面上的刷新按钮；等异步取数落地后返回最后一条提示 */
async function clickRefresh(wrapper: VueWrapper, label: string): Promise<void> {
  const button = wrapper.findAll('button').find((item) => item.text().includes(label))
  expect(button, `没有找到「${label}」按钮`).toBeTruthy()
  await button?.trigger('click')
  await flushPromises()
}

describe('公众号管理：刷新列表', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('读到订阅时提示刷新成功与公众号数量', async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/api/feeds') {
        return [feed(), feed({ id: 'feed-2', name: '另一个公众号' })]
      }
      if (url === '/api/config') {
        return config('source-token')
      }
      throw new Error(`未预期的请求：${url}`)
    })
    const { wrapper, ui } = mountView(Feeds)
    await flushPromises()

    await clickRefresh(wrapper, '刷新列表')

    expect(ui.toasts).toHaveLength(1)
    expect(ui.toasts[0].tone).toBe('success')
    expect(ui.toasts[0].title).toBe('刷新成功')
    expect(ui.toasts[0].description).toBe('共 2 个公众号')
  })

  it('没有访问令牌时给警告，不谎报刷新成功', async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/api/feeds') {
        return []
      }
      if (url === '/api/config') {
        return config('')
      }
      throw new Error(`未预期的请求：${url}`)
    })
    const { wrapper, ui } = mountView(Feeds)
    await flushPromises()

    await clickRefresh(wrapper, '刷新列表')

    expect(ui.toasts).toHaveLength(1)
    expect(ui.toasts[0].tone).toBe('warning')
    expect(ui.toasts[0].title).toBe('刷新完成，但访问令牌未配置')
  })
})

describe('数据源状态：刷新状态', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('数据源可达时提示刷新成功', async () => {
    mockedGet.mockResolvedValue(health())
    const { wrapper, ui } = mountView(Source)
    await flushPromises()

    await clickRefresh(wrapper, '刷新状态')

    expect(ui.toasts).toHaveLength(1)
    expect(ui.toasts[0].tone).toBe('success')
    expect(ui.toasts[0].title).toBe('刷新成功')
    expect(ui.toasts[0].description).toBe('数据源运行中')
  })

  it('数据源不可达时给警告', async () => {
    mockedGet.mockResolvedValue(health({ healthy: false }))
    const { wrapper, ui } = mountView(Source)
    await flushPromises()

    await clickRefresh(wrapper, '刷新状态')

    expect(ui.toasts).toHaveLength(1)
    expect(ui.toasts[0].tone).toBe('warning')
    expect(ui.toasts[0].title).toBe('刷新完成，但数据源不可达')
  })
})

describe('任务 store：refresh 的返回值', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockedGet.mockReset()
  })

  it('读取成功返回 true，并清掉上一次读取失败留下的错误', async () => {
    mockedGet.mockRejectedValueOnce(new Error('服务暂时不可用'))
    const jobs = useJobsStore()
    expect(await jobs.refresh()).toBe(false)
    expect(jobs.error).toContain('服务暂时不可用')

    mockedGet.mockResolvedValueOnce({ current: null, recent: [] } as JobList)
    expect(await jobs.refresh()).toBe(true)
    expect(jobs.error).toBe('')
  })
})
