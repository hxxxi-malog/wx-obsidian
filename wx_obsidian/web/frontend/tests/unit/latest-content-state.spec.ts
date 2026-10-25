/**
 * 最新文章页的正文状态契约：
 *  1. 「无正文」拆成 补抓中 / 待补抓 / 补抓失败 三种标签，补抓失败显示已试次数；
 *  2. 只有待补抓与补抓失败给「重试」，重试按钮不能触发整行打开原文；
 *  3. 触发成功弹提示，并在 5 秒、25 秒各重拉一次列表；触发失败走 toast.error；
 *  4. 补抓进度条读 feed-sync store 的 backfill_* 字段。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { FeedSyncState, SourceArticleItem, SourceArticlePage } from '@/lib/api/types'
import { useUiStore } from '@/stores/ui'
import Latest from '@/views/Latest.vue'

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/client')>()
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn() }
})

import { apiGet, apiPost } from '@/lib/api/client'

const mockedGet = vi.mocked(apiGet)
const mockedPost = vi.mocked(apiPost)

function article(overrides: Partial<SourceArticleItem> = {}): SourceArticleItem {
  const articleId = overrides.article_id ?? 'article-1'
  return {
    article_id: articleId,
    // 上游内部 ID 与列表里的短链 token 故意不同：重试必须用这个
    upstream_id: `upstream-${articleId}`,
    title: '一篇测试文章',
    url: 'https://mp.weixin.qq.com/s/example',
    account: '测试公众号',
    account_id: 'MP_TEST',
    published_at: null,
    cover: '',
    has_content: false,
    content_state: 'pending',
    content_attempts: 0,
    processed: false,
    ...overrides,
  }
}

function page(items: SourceArticleItem[]): SourceArticlePage {
  return { total: items.length, offset: 0, limit: 30, token_configured: true, items }
}

function syncState(overrides: Partial<FeedSyncState> = {}): FeedSyncState {
  return {
    running: false,
    total: 0,
    triggered: 0,
    skipped: 0,
    failed: 0,
    started_at: '',
    finished_at: '',
    error: '',
    backfill_running: false,
    backfill_pending: 0,
    backfill_refreshed: 0,
    backfill_failed: 0,
    backfill_last_run_at: '',
    ...overrides,
  }
}

/** 只桩网络层：文章列表、公众号列表、同步状态都走真实渲染路径 */
function mockApi(items: SourceArticleItem[], sync: FeedSyncState = syncState()): void {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/api/feeds') {
      return []
    }
    if (url.startsWith('/api/source/articles')) {
      return page(items)
    }
    if (url === '/api/source/sync') {
      return sync
    }
    throw new Error(`未预期的请求：${url}`)
  })
}

function mountLatest(): { wrapper: VueWrapper; ui: ReturnType<typeof useUiStore> } {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/settings', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ],
  })
  const wrapper = mount(Latest, { global: { plugins: [pinia, router] } })
  return { wrapper, ui: useUiStore() }
}

function tagByText(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('span').find((node) => node.text() === label)
}

function retryButtons(wrapper: VueWrapper) {
  return wrapper.findAll('button').filter((node) => node.text() === '重试')
}

function listCalls(): number {
  return mockedGet.mock.calls.filter(([url]) => String(url).startsWith('/api/source/articles'))
    .length
}

const THREE_STATES = [
  article({ article_id: 'a-fetching', content_state: 'fetching' }),
  article({ article_id: 'a-pending', content_state: 'pending' }),
  article({ article_id: 'a-failed', content_state: 'failed', content_attempts: 3 }),
  article({ article_id: 'a-ready', has_content: true, content_state: 'ready' }),
]

describe('最新文章：正文状态标签', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedPost.mockReset()
  })

  it('三种无正文状态各自渲染标签，不再出现「无正文」', async () => {
    mockApi(THREE_STATES)
    const { wrapper } = mountLatest()
    await flushPromises()

    expect(wrapper.text()).not.toContain('无正文')
    expect(tagByText(wrapper, '补抓中')?.classes()).toContain('text-info')
    expect(tagByText(wrapper, '待补抓')?.classes()).toContain('text-warning')
    expect(tagByText(wrapper, '补抓失败')?.classes()).toContain('text-danger')
  })

  it('补抓失败显示已尝试次数', async () => {
    mockApi(THREE_STATES)
    const { wrapper } = mountLatest()
    await flushPromises()

    expect(wrapper.text()).toContain('已试 3 次')
  })

  it('只有待补抓与补抓失败给重试按钮，且按钮不在原文链接里', async () => {
    mockApi(THREE_STATES)
    const { wrapper } = mountLatest()
    await flushPromises()

    expect(retryButtons(wrapper)).toHaveLength(2)
    expect(wrapper.findAll('a button')).toHaveLength(0)
  })

  it('补抓中与已有正文不给重试按钮', async () => {
    mockApi([
      article({ article_id: 'a-fetching', content_state: 'fetching' }),
      article({ article_id: 'a-ready', has_content: true, content_state: 'ready' }),
    ])
    const { wrapper } = mountLatest()
    await flushPromises()

    expect(retryButtons(wrapper)).toHaveLength(0)
  })
})

describe('最新文章：补抓重试', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedPost.mockReset()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('触发成功后提示，并在 5 秒、25 秒各重拉一次列表', async () => {
    mockApi([article({ article_id: 'a-pending', content_state: 'pending' })])
    mockedPost.mockResolvedValue({ ok: true, outcome: 'triggered', message: '已开始补抓' })
    const { wrapper, ui } = mountLatest()
    await flushPromises()
    expect(listCalls()).toBe(1)

    await retryButtons(wrapper)[0].trigger('click')
    await flushPromises()

    expect(mockedPost).toHaveBeenCalledWith('/api/source/articles/upstream-a-pending/refresh')
    expect(ui.toasts).toHaveLength(1)
    expect(ui.toasts[0].tone).toBe('success')
    expect(ui.toasts[0].title).toBe('已开始补抓正文')
    expect(listCalls()).toBe(1)

    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(listCalls()).toBe(2)

    await vi.advanceTimersByTimeAsync(25000)
    await flushPromises()
    expect(listCalls()).toBe(3)
  })

  it('上游已有任务时按 skipped 处理，同样提示并重拉', async () => {
    mockApi([article({ article_id: 'a-failed', content_state: 'failed', content_attempts: 2 })])
    mockedPost.mockResolvedValue({ ok: true, outcome: 'skipped', message: '已有补抓任务在执行' })
    const { wrapper, ui } = mountLatest()
    await flushPromises()

    await retryButtons(wrapper)[0].trigger('click')
    await flushPromises()

    expect(ui.toasts[0].tone).toBe('success')
    expect(ui.toasts[0].title).toBe('补抓任务已在执行')
    expect(mockedPost).toHaveBeenCalledWith('/api/source/articles/upstream-a-failed/refresh')

    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(listCalls()).toBe(2)
  })

  it('触发失败给 toast.error，并且不安排重拉', async () => {
    mockApi([article({ article_id: 'a-failed', content_state: 'failed', content_attempts: 2 })])
    mockedPost.mockResolvedValue({ ok: false, outcome: 'failed', message: '上游拒绝补抓' })
    const { wrapper, ui } = mountLatest()
    await flushPromises()

    await retryButtons(wrapper)[0].trigger('click')
    await flushPromises()

    expect(ui.toasts).toHaveLength(1)
    expect(ui.toasts[0].tone).toBe('danger')
    expect(ui.toasts[0].description).toBe('上游拒绝补抓')
    expect(mockedPost).toHaveBeenCalledWith('/api/source/articles/upstream-a-failed/refresh')

    await vi.advanceTimersByTimeAsync(30000)
    await flushPromises()
    expect(listCalls()).toBe(1)
  })

  it('请求本身失败也给 toast.error', async () => {
    mockApi([article({ article_id: 'a-pending', content_state: 'pending' })])
    mockedPost.mockRejectedValue(new Error('网络断了'))
    const { wrapper, ui } = mountLatest()
    await flushPromises()

    await retryButtons(wrapper)[0].trigger('click')
    await flushPromises()

    expect(ui.toasts).toHaveLength(1)
    expect(ui.toasts[0].tone).toBe('danger')
    expect(ui.toasts[0].description).toContain('网络断了')
    expect(mockedPost).toHaveBeenCalledWith('/api/source/articles/upstream-a-pending/refresh')
  })

  it('组件卸载后清掉延时重拉定时器', async () => {
    mockApi([article({ article_id: 'a-pending', content_state: 'pending' })])
    mockedPost.mockResolvedValue({ ok: true, outcome: 'triggered', message: '已开始补抓' })
    const { wrapper } = mountLatest()
    await flushPromises()

    await retryButtons(wrapper)[0].trigger('click')
    await flushPromises()
    expect(listCalls()).toBe(1)

    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(30000)
    await flushPromises()
    expect(listCalls()).toBe(1)
  })
})

describe('最新文章：补抓进度条', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedPost.mockReset()
  })

  it('backfill_running 为真时显示进度与篇数', async () => {
    mockApi(
      [article({ article_id: 'a-pending', content_state: 'pending' })],
      syncState({
        backfill_running: true,
        backfill_pending: 12,
        backfill_refreshed: 3,
        backfill_failed: 1,
      }),
    )
    const { wrapper } = mountLatest()
    await flushPromises()

    const bar = wrapper.get('[aria-label="正文补抓进度"]')
    expect(bar.text()).toContain('正在补充正文：待补抓 12 篇，已触发 3 篇')
    expect(bar.text()).toContain('1 篇触发失败')
    expect(bar.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('25')
  })

  it('补抓没在跑时不显示进度条', async () => {
    mockApi([article({ article_id: 'a-pending', content_state: 'pending' })])
    const { wrapper } = mountLatest()
    await flushPromises()

    expect(wrapper.find('[aria-label="正文补抓进度"]').exists()).toBe(false)
  })

  it('backfill_running 为真但三项计数全为 0 时不渲染进度条', async () => {
    mockApi(
      [article({ article_id: 'a-pending', content_state: 'pending' })],
      syncState({
        backfill_running: true,
        backfill_pending: 0,
        backfill_refreshed: 0,
        backfill_failed: 0,
      }),
    )
    const { wrapper } = mountLatest()
    await flushPromises()

    expect(wrapper.find('[aria-label="正文补抓进度"]').exists()).toBe(false)
  })

  it('backfill_error 非空时显示中止原因', async () => {
    mockApi(
      [article({ article_id: 'a-pending', content_state: 'pending' })],
      syncState({ backfill_error: '访问令牌失效，补抓中止' }),
    )
    const { wrapper } = mountLatest()
    await flushPromises()

    const bar = wrapper.get('[aria-label="正文补抓进度"]')
    expect(bar.text()).toContain('补抓未能继续：访问令牌失效，补抓中止')
  })

  it('backfill_error 为空串时不渲染中止原因', async () => {
    mockApi(
      [article({ article_id: 'a-pending', content_state: 'pending' })],
      syncState({ backfill_running: true, backfill_pending: 3, backfill_refreshed: 1 }),
    )
    const { wrapper } = mountLatest()
    await flushPromises()

    expect(wrapper.text()).not.toContain('补抓未能继续')
  })
})
