/**
 * 所有后端路径的唯一事实来源。
 *
 * 为什么集中：
 *  1. `scripts/style-guard.mjs` 禁止其它文件出现 `/api/` 字面量，
 *     `tests/test_web/test_frontend_contract.py` 会拿这里的路径去比对 FastAPI 路由表——
 *     后端一旦改路由，测试立刻红灯，而不是等用户点到 404。
 *  2. 路径参数统一 `encodeURIComponent`（article_id / job_id / feed_id 都可能含特殊字符）。
 */

import type {
  ArticleQuery,
  ArticleSort,
  ConnectionTestTarget,
  SortOrder,
} from './types'

type QueryValue = string | number | boolean | undefined | null

/** 把对象编成查询串，跳过空值（空串等于「不过滤」，与后端约定一致） */
export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') {
      return
    }
    search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

const seg = (value: string): string => encodeURIComponent(value)

export interface ArticleListParams extends Omit<ArticleQuery, 'sort' | 'order'> {
  sort?: ArticleSort
  order?: SortOrder
}

export const endpoints = {
  ping: () => '/api/ping',
  health: () => '/api/health',
  stats: () => '/api/stats',
  failed: () => '/api/failed',
  shutdown: () => '/api/shutdown',

  jobs: {
    list: () => '/api/jobs',
    create: () => '/api/jobs',
    detail: (jobId: string) => `/api/jobs/${seg(jobId)}`,
    cancel: (jobId: string) => `/api/jobs/${seg(jobId)}/cancel`,
    /** SSE：token 必须走查询参数（EventSource 不能设自定义头） */
    events: (jobId: string) => `/api/jobs/${seg(jobId)}/events`,
  },

  articles: {
    list: (params: ArticleListParams = {}) =>
      `/api/articles${buildQuery({
        q: params.q,
        category: params.category,
        status: params.status,
        limit: params.limit,
        offset: params.offset,
        sort: params.sort,
        order: params.order,
      })}`,
    detail: (articleId: string) => `/api/articles/${seg(articleId)}`,
    remove: (articleId: string) => `/api/articles/${seg(articleId)}`,
    bulkDelete: () => '/api/articles/bulk-delete',
    open: (articleId: string) => `/api/articles/${seg(articleId)}/open`,
    retry: () => '/api/articles/retry',
  },

  config: {
    get: () => '/api/config',
    save: () => '/api/config',
    test: () => '/api/config/test',
  },

  source: {
    health: () => '/api/source/health',
    account: () => '/api/source/account',
    loginUrl: () => '/api/source/login-url',
    refresh: () => '/api/source/refresh',
    /** 真实探针：向上游要一次公众号信息，确认微信读书凭据还能用 */
    probe: () => '/api/source/probe',
    /** 扫码登录：取二维码（data URL） */
    loginQr: () => '/api/source/login-qr',
    /** 扫码登录：轮询扫码状态 */
    loginQrStatus: () => '/api/source/login-qr/status',
    /** 扫码登录：登录成功后收尾（后端顺带触发公众号采集） */
    loginQrOver: () => '/api/source/login-qr/over',
    /** 登录后后端自动触发的公众号采集进度 */
    sync: () => '/api/source/sync',
  },

  feeds: {
    list: () => '/api/feeds',
    add: () => '/api/feeds',
    remove: (feedId: string) => `/api/feeds/${seg(feedId)}`,
  },

  logs: {
    list: (since = 0) => `/api/logs${buildQuery({ since })}`,
    /** SSE：与任务事件流同一套写法 */
    events: (since = 0) => `/api/logs/events${buildQuery({ since })}`,
  },

  fs: {
    list: (path: string) => `/api/fs/list${buildQuery({ path })}`,
    pick: () => '/api/fs/pick',
  },

  /** POST /api/config/test 的 target 取值 */
  testTargets: ['llm', 'vision', 'source', 'all'] as const satisfies readonly ConnectionTestTarget[],
} as const
