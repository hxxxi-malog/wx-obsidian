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
  normalizeTokenInput,
  onUnauthorized,
  setToken,
  verifyToken,
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

describe('token 恢复通道（解锁对话框）', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    sessionStorage.clear()
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('setToken 去空白后写入；空串等于清除', () => {
    setToken('  abc  ')
    expect(getToken()).toBe('abc')
    setToken('   ')
    expect(getToken()).toBe('')
  })

  it('normalizeTokenInput 同时接受裸 token 与启动器打印的完整地址', () => {
    expect(normalizeTokenInput('  abc  ')).toBe('abc')
    expect(normalizeTokenInput('http://127.0.0.1:8765/?token=xyz')).toBe('xyz')
    expect(normalizeTokenInput('http://127.0.0.1:8765/settings?first_run=1&token=a%2Bb')).toBe('a+b')
    expect(normalizeTokenInput('http://127.0.0.1:8765/')).toBe('http://127.0.0.1:8765/')
  })

  it('verifyToken 用候选 token 探测配置快照，且校验通过前不覆盖存储', async () => {
    sessionStorage.setItem(TOKEN_KEY, 'old')
    fetchMock.mockResolvedValue(jsonResponse({ llm: {} }))
    await expect(verifyToken(' candidate ')).resolves.toBe(true)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/config')
    expect((init.headers as Record<string, string>)['X-WX-Token']).toBe('candidate')
    expect(getToken()).toBe('old')
  })

  it('verifyToken 对 401 返回 false（界面据此报「token 不对」而不是抛错）', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'unauthorized' }, 401))
    await expect(verifyToken('nope')).resolves.toBe(false)
  })

  it('verifyToken 对 5xx 抛 ApiError（区分「服务坏了」与「token 错」）', async () => {
    fetchMock.mockResolvedValue(new Response('boom', { status: 500 }))
    await expect(verifyToken('abc')).rejects.toMatchObject({ status: 500 })
  })

  it('verifyToken 空输入直接 false，不发请求', async () => {
    await expect(verifyToken('   ')).resolves.toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('401 广播（界面据此打开解锁框）', () => {
  const fetchMock = vi.fn()
  /** 反订阅函数：断言结束后必须回归，否则会污染后续用例 */
  const stops: Array<() => void> = []

  beforeEach(() => {
    sessionStorage.clear()
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    stops.splice(0).forEach((stop) => stop())
  })

  it('401 通知订阅者，反订阅后不再通知', async () => {
    const seen: string[] = []
    stops.push(onUnauthorized(() => seen.push('401')))
    fetchMock.mockResolvedValue(jsonResponse({ error: 'unauthorized' }, 401))
    await expect(apiGet('/api/health')).rejects.toMatchObject({ status: 401 })
    expect(seen).toEqual(['401'])
    stops.splice(0).forEach((stop) => stop())
    await expect(apiGet('/api/health')).rejects.toMatchObject({ status: 401 })
    expect(seen).toEqual(['401'])
  })

  it('非 401 错误不广播（避免把普通失败误判成未授权）', async () => {
    const seen: string[] = []
    stops.push(onUnauthorized(() => seen.push('401')))
    fetchMock.mockResolvedValue(jsonResponse({ error: 'boom' }, 500))
    await expect(apiGet('/api/health')).rejects.toMatchObject({ status: 500 })
    expect(seen).toEqual([])
  })
})

describe('errorMessage 既有文案', () => {
  it('401 只描述状态，恢复动作交给全局横幅与解锁框', () => {
    expect(errorMessage(new ApiError(401, 'unauthorized'))).toBe(
      '未授权：当前标签页没有访问令牌（token）',
    )
  })

  it('其它错误原样透出', () => {
    expect(errorMessage(new ApiError(400, '不支持的配置键'))) .toBe('不支持的配置键')
    expect(errorMessage(new Error('boom'))).toBe('boom')
    expect(errorMessage('text')).toBe('text')
  })
})
