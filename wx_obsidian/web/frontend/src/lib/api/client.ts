/**
 * API 客户端：统一带 token、统一错误信息。
 *
 * token 来源（与后端 SecurityMiddleware 的约定，不能改）：
 *  启动器打开的 URL 里带 `?token=`，取出后立即从地址栏抹掉并写进 sessionStorage，
 *  避免留在历史记录与 Referer 里。REST 走 `X-WX-Token` 头；SSE 只能走查询参数
 *  （EventSource 无法设自定义头）。开发态走 Vite 代理时同样成立。
 *
 * 「地址栏被抹掉」意味着：收藏夹、历史记录、新标签页里打开的裸地址都不带 token。
 * 所以除 `initToken()` 之外还有一条**恢复**通道：`setToken()` 由解锁对话框调用，
 * 先用 `verifyToken()` 探测真伪，再整页刷新让所有请求重来。安全性不变——
 * token 仍然只存在本标签页的 sessionStorage 里，服务端也从不把它回传给未鉴权请求。
 */

import { endpoints } from './endpoints'

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

/** 记住 token（解锁对话框用）。空串等价于清除，避免把空白写进存储。 */
export function setToken(token: string): void {
  const value = token.trim()
  if (!value) {
    clearToken()
    return
  }
  sessionStorage.setItem(TOKEN_KEY, value)
}

/** 清掉本标签页的 token（换人/换实例时用） */
export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}

/**
 * 把用户粘贴的内容归一成 token。
 *
 * 启动器打印的是完整地址（`http://127.0.0.1:8765/?token=…`），文档里写的也是地址，
 * 用户最可能整条复制。这里同时接受裸 token 与带 `?token=` 的地址。
 */
export function normalizeTokenInput(raw: string): string {
  const value = raw.trim()
  const queryStart = value.indexOf('?')
  if (queryStart === -1) {
    return value
  }
  const token = new URLSearchParams(value.slice(queryStart + 1)).get('token')
  return token ? token.trim() : value
}

/** 401 订阅者：token 缺失或失效时被通知（全局只挂一次，见 `stores/auth.ts`） */
type UnauthorizedHandler = () => void
const unauthorizedHandlers = new Set<UnauthorizedHandler>()

/** 订阅 401；返回反订阅函数（组件卸载时调用）。 */
export function onUnauthorized(handler: UnauthorizedHandler): () => void {
  unauthorizedHandlers.add(handler)
  return () => {
    unauthorizedHandlers.delete(handler)
  }
}

function notifyUnauthorized(): void {
  unauthorizedHandlers.forEach((handler) => handler())
}

/**
 * 校验候选 token 是否被当前服务接受。**只探测，不写入存储**——
 * 写进去之前先确认能用，避免「错了还覆盖掉本来能用的那个」。
 *
 * 探针用配置快照：需要鉴权的最轻量 GET，且没有任何副作用。
 * 401/403 → `false`；其它失败一律抛错，让调用方区分「token 错」与「服务不可用」。
 */
export async function verifyToken(candidate: string): Promise<boolean> {
  const value = candidate.trim()
  if (!value) {
    return false
  }
  const response = await fetch(endpoints.config.get(), {
    headers: { 'X-WX-Token': value },
    cache: 'no-store',
  })
  if (response.ok) {
    return true
  }
  if (response.status === 401 || response.status === 403) {
    return false
  }
  throw await readError(response)
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
    const error = await readError(response)
    // 401 统一广播：界面据此弹出「输入 token」，页面不必各写一遍提示
    if (response.status === 401) {
      notifyUnauthorized()
    }
    throw error
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
      // 这里只描述状态；「怎么恢复」由全局横幅/解锁对话框给出（见 TokenGate.vue）
      return '未授权：当前标签页没有访问令牌（token）'
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
