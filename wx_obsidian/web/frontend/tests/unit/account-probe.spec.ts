/**
 * 账号页的探针契约：
 *  1. state=degraded（会话失效但正文已由微信原文兜底）不弹扫码对话框，手动刷新时给警告；
 *  2. state=expired 仍按既有行为自动弹扫码；
 *  3. content_source / content_error 有值就展示，content_ready=false 给出正文抓取失败提示，
 *     content_ready=null（上游旧版本）保持旧行为、不误报失败。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AccountProbe, AccountStatus } from '@/lib/api/types'
import { useUiStore } from '@/stores/ui'
import Account from '@/views/Account.vue'

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/client')>()
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn() }
})

import { apiGet, apiPost } from '@/lib/api/client'

const mockedGet = vi.mocked(apiGet)
const mockedPost = vi.mocked(apiPost)

function account(): AccountStatus {
  return {
    is_logged_in: true,
    username: 'reader',
    expire_at: '2026-11-01T00:00:00+08:00',
    need_refresh: false,
    queried: true,
    token: {
      configured: true,
      expires_at: '2026-11-01T00:00:00+08:00',
      expired: false,
    },
  }
}

function probe(overrides: Partial<AccountProbe> = {}): AccountProbe {
  return {
    state: 'degraded',
    message: '微信读书会话已失效，正文改走微信原文兜底',
    feed_name: '测试公众号',
    checked_at: '2026-10-23T17:00:00+08:00',
    content_ready: true,
    content_source: 'wechat',
    content_error: '',
    ...overrides,
  }
}

/** 只桩网络层：账号快照、探针、二维码接口 */
function mockApi(result: AccountProbe): void {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/api/source/account') {
      return account()
    }
    if (url === '/api/source/login-qr') {
      return { image: 'data:image/png;base64,ZmFrZQ==', uid: 'uid-1' }
    }
    if (url === '/api/source/login-qr/status') {
      return { login_status: false, message: '等待扫码', logic_code: '' }
    }
    throw new Error(`未预期的请求：${url}`)
  })
  mockedPost.mockResolvedValue(result)
}

function mountAccount(): { wrapper: VueWrapper; ui: ReturnType<typeof useUiStore> } {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(Account, { global: { plugins: [pinia] } })
  return { wrapper, ui: useUiStore() }
}

function qrRequests(): number {
  return mockedGet.mock.calls.filter(([url]) => String(url).includes('/api/source/login-qr'))
    .length
}

function buttonByText(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((node) => node.text().includes(label))
}

describe('账号页：degraded 探针', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedPost.mockReset()
  })

  it('进入页面读到 degraded 时不弹扫码对话框、也不弹提示', async () => {
    mockApi(probe())
    const { wrapper, ui } = mountAccount()
    await flushPromises()

    expect(qrRequests()).toBe(0)
    expect(ui.toasts).toHaveLength(0)
    expect(wrapper.text()).toContain('会话失效')
    expect(wrapper.text()).toContain('微信读书会话已失效；正文已由微信原文兜底')
  })

  it('手动刷新时给警告提示，仍不弹扫码', async () => {
    mockApi(probe())
    const { wrapper, ui } = mountAccount()
    await flushPromises()

    await buttonByText(wrapper, '刷新状态')?.trigger('click')
    await flushPromises()

    expect(ui.toasts).toHaveLength(1)
    expect(ui.toasts[0].tone).toBe('warning')
    expect(ui.toasts[0].title).toContain('微信读书会话已失效')
    expect(ui.toasts[0].description).toContain('扫码重新登录')
    expect(qrRequests()).toBe(0)
  })

  it('expired 仍按既有行为自动弹出扫码', async () => {
    mockApi(probe({ state: 'expired', message: '微信读书会话已过期', content_source: '' }))
    const { wrapper } = mountAccount()
    await flushPromises()

    expect(qrRequests()).toBe(1)
    wrapper.unmount()
  })
})

describe('账号页：正文抓取状态', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedPost.mockReset()
  })

  it('展示正文来源与正文错误，content_ready=false 给出抓取失败提示', async () => {
    mockApi(
      probe({
        state: 'valid',
        message: '凭据有效',
        content_ready: false,
        content_source: 'weread',
        content_error: '微信读书请求超时',
      }),
    )
    const { wrapper } = mountAccount()
    await flushPromises()

    expect(wrapper.text()).toContain('正文来源')
    expect(wrapper.text()).toContain('微信读书接口')
    expect(wrapper.text()).toContain('正文错误')
    expect(wrapper.text()).toContain('微信读书请求超时')
    expect(wrapper.text()).toContain('正文抓取失败')
  })

  it('微信原文兜底时说明正文来源', async () => {
    mockApi(probe({ state: 'valid', message: '凭据有效', content_source: 'wechat' }))
    const { wrapper } = mountAccount()
    await flushPromises()

    expect(wrapper.text()).toContain('正文来源')
    expect(wrapper.text()).toContain('微信原文兜底')
    expect(wrapper.text()).not.toContain('正文抓取失败')
  })

  it('content_ready=null（上游旧版本）保持旧行为，不误报正文失败', async () => {
    mockApi(
      probe({
        state: 'valid',
        message: '凭据有效',
        content_ready: null,
        content_source: '',
        content_error: '',
      }),
    )
    const { wrapper } = mountAccount()
    await flushPromises()

    expect(wrapper.text()).not.toContain('正文抓取失败')
    expect(wrapper.text()).not.toContain('正文来源')
  })
})
