import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  apiDelete,
  apiGet,
  apiPost,
  errorMessage,
  eventStreamUrl,
  getToken,
  hasCode,
  initToken,
} from '@/lib/api/client'

const TOKEN_KEY = 'wx-obsidian-token'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('token 链路（不能改的契约）', () => {
  beforeEach(() => {
    sessionStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  it('从 ?token= 取值、写 sessionStorage 并抹掉地址栏', () => {
    window.history.replaceState({}, '', '/?token=secret-123&keep=1')
    initToken()
    expect(getToken()).toBe('secret-123')
    expect(window.location.search).toBe('?keep=1')
  })

  it('没有 token 参数时不动地址栏', () => {
    window.history.replaceState({}, '', '/articles?q=rag')
    initToken()
    expect(getToken()).toBe('')
    expect(window.location.search).toBe('?q=rag')
  })

  it('eventStreamUrl 用查询参数带 token（EventSource 无法设头）', () => {
    sessionStorage.setItem(TOKEN_KEY, 't k')
    expect(eventStreamUrl('/api/jobs/x/events')).toBe('/api/jobs/x/events?token=t%20k')
    expect(eventStreamUrl('/api/logs/events?since=3')).toBe('/api/logs/events?since=3&token=t%20k')
  })

  it('无 token 时 eventStreamUrl 不加参数', () => {
    expect(eventStreamUrl('/api/logs/events')).toBe('/api/logs/events')
  })
})

describe('请求层', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    sessionStorage.clear()
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('带上 X-WX-Token 与 JSON Content-Type', async () => {
    sessionStorage.setItem(TOKEN_KEY, 'abc')
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))
    await apiPost('/api/config', { patch: {} })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>)['X-WX-Token']).toBe('abc')
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
    expect(init.body).toBe(JSON.stringify({ patch: {} }))
  })

  it('GET 不带 Content-Type，DELETE 用 DELETE 方法', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({ items: [] })))
    await apiGet('/api/articles')
    await apiDelete('/api/articles/1')
    const [, first] = fetchMock.mock.calls[0] as [string, RequestInit]
    const [, second] = fetchMock.mock.calls[1] as [string, RequestInit]
    expect(first.headers).not.toHaveProperty('Content-Type')
    expect(second.method).toBe('DELETE')
  })

  it('错误体里优先用 detail，并保留机器可读的 code', async () => {
    // 每次调用都要一个新 Response：Response 的 body 只能消费一次
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse({ error: 'job_busy', detail: '已有任务在运行' }, 409)),
    )
    await expect(apiPost('/api/jobs')).rejects.toMatchObject({
      status: 409,
      code: 'job_busy',
      message: '已有任务在运行',
    })
    try {
      await apiPost('/api/jobs')
    } catch (err) {
      expect(hasCode(err, 'job_busy')).toBe(true)
      expect(hasCode(err, 'other')).toBe(false)
    }
  })

  it('非 JSON 错误体退回到状态码', async () => {
    fetchMock.mockResolvedValue(new Response('boom', { status: 502 }))
    await expect(apiGet('/api/health')).rejects.toMatchObject({ status: 502, message: 'HTTP 502' })
  })

  it('204 返回 undefined', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(apiDelete('/api/feeds/1')).resolves.toBeUndefined()
  })

  it('网络失败归一成 ApiError(0) 且文案可读', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    try {
      await apiGet('/api/health')
      expect.unreachable('应当抛错')
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).status).toBe(0)
      expect((err as ApiError).message).toContain('无法连接本地服务')
    }
  })
})

describe('errorMessage 既有文案', () => {
  it('401 给出去启动器地址进入的指引', () => {
    expect(errorMessage(new ApiError(401, 'unauthorized'))).toBe(
      '未授权：请从应用启动器打开的地址进入（URL 需带 token）',
    )
  })

  it('其它错误原样透出', () => {
    expect(errorMessage(new ApiError(400, '不支持的配置键'))) .toBe('不支持的配置键')
    expect(errorMessage(new Error('boom'))).toBe('boom')
    expect(errorMessage('text')).toBe('text')
  })
})
