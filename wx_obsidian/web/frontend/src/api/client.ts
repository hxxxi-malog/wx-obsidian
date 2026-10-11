/**
 * API 客户端：统一带 token、统一错误信息。
 *
 * token 来源：启动器打开的 URL 里带 `?token=`（存进 sessionStorage 后立即从地址栏抹掉，
 * 避免留在历史记录与 Referer 里）。开发态走 Vite 代理时同样支持。
 */

const TOKEN_KEY = 'wx-obsidian-token'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** 应用启动时调用：从 URL 捕获 token 并清理地址栏。 */
export function initToken(): void {
  const url = new URL(window.location.href)
  const token = url.searchParams.get('token')
  if (!token) {
    return
  }
  sessionStorage.setItem(TOKEN_KEY, token)
  url.searchParams.delete('token')
  window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)
}

export function getToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? ''
}

async function describeError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { error?: string; detail?: string }
    return payload.detail || payload.error || `HTTP ${response.status}`
  } catch {
    return `HTTP ${response.status}`
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = { ...((init.headers as Record<string, string>) ?? {}) }
  if (token) {
    headers['X-WX-Token'] = token
  }
  if (init.body) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    throw new ApiError(response.status, await describeError(response))
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
}

export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) })
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' })
}

/**
 * SSE 事件流地址。
 *
 * 用 EventSource 而不是 fetch 流：浏览器会自动重连并自动带上 Last-Event-ID。
 * 代价是它无法设自定义头，故 token 只能走查询参数。
 * 注意：流正常结束（任务完成）时 EventSource 仍会自动重连，调用方必须在终态事件里 close()。
 */
export function eventStreamUrl(path: string): string {
  const token = getToken()
  const separator = path.includes('?') ? '&' : '?'
  return token ? `${path}${separator}token=${encodeURIComponent(token)}` : path
}

export function buildQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') {
      search.set(key, String(value))
    }
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 401) {
    return '未授权：请从应用启动器打开的地址进入（URL 需带 token）'
  }
  return err instanceof Error ? err.message : String(err)
}
