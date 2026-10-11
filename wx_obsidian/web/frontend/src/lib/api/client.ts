/**
 * API 客户端：统一带 token、统一错误信息。
 *
 * token 来源（与后端 SecurityMiddleware 的约定，不能改）：
 *  启动器打开的 URL 里带 `?token=`，取出后立即从地址栏抹掉并写进 sessionStorage，
 *  避免留在历史记录与 Referer 里。REST 走 `X-WX-Token` 头；SSE 只能走查询参数
 *  （EventSource 无法设自定义头）。开发态走 Vite 代理时同样成立。
 */

const TOKEN_KEY = 'wx-obsidian-token'

/** 后端返回的结构化错误体 */
interface ErrorBody {
  error?: string
  detail?: string
}

export class ApiError extends Error {
  /** HTTP 状态码；网络层失败时为 0 */
  readonly status: number
  /** 后端的机器可读错误码，例如 job_busy / vault_not_registered / not_supported */
  readonly code: string

  constructor(status: number, message: string, code = '') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
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

export function hasToken(): boolean {
  return getToken() !== ''
}

async function readError(response: Response): Promise<ApiError> {
  let body: ErrorBody = {}
  try {
    body = (await response.json()) as ErrorBody
  } catch {
    // 非 JSON 响应（代理错误页等）：退回状态码
  }
  const message = body.detail || body.error || `HTTP ${response.status}`
  return new ApiError(response.status, message, body.error ?? '')
}

export interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
}

/** 统一请求入口：token、JSON 编解码、错误归一 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) {
    headers['X-WX-Token'] = token
  }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  let response: Response
  try {
    response = await fetch(path, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err
    }
    throw new ApiError(0, '无法连接本地服务：请确认应用仍在运行')
  }

  if (!response.ok) {
    throw await readError(response)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { signal })
}

export function apiPost<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'POST', body, signal })
}

export function apiPut<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'PUT', body, signal })
}

export function apiDelete<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'DELETE', signal })
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

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) {
      return '未授权：请从应用启动器打开的地址进入（URL 需带 token）'
    }
    return err.message
  }
  if (err instanceof Error) {
    return err.message
  }
  return String(err)
}

/** 需要按后端错误码分支时的便捷判断（例如 job_busy / not_supported） */
export function hasCode(err: unknown, code: string): boolean {
  return err instanceof ApiError && err.code === code
}
