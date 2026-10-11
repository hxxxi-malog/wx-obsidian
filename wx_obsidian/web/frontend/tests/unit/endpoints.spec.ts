import { describe, expect, it } from 'vitest'

import { buildQuery, endpoints } from '@/lib/api/endpoints'

describe('buildQuery', () => {
  it('跳过空串、undefined 与 null，保留 0', () => {
    expect(buildQuery({ a: 'x', b: '', c: undefined, d: null, e: 0 })).toBe('?a=x&e=0')
  })

  it('没有有效参数时返回空串', () => {
    expect(buildQuery({ a: '', b: undefined })).toBe('')
  })

  it('做 URL 编码', () => {
    expect(buildQuery({ q: 'AI 架构' })).toBe('?q=AI+%E6%9E%B6%E6%9E%84')
  })
})

describe('endpoints 路径', () => {
  it('文章列表拼装查询串（含排序）', () => {
    expect(endpoints.articles.list({ q: 'rag', limit: 20, offset: 40, sort: 'title', order: 'asc' })).toBe(
      '/api/articles?q=rag&limit=20&offset=40&sort=title&order=asc',
    )
  })

  it('不传参数时只有路径', () => {
    expect(endpoints.articles.list()).toBe('/api/articles')
    expect(endpoints.articles.list({ q: '' })).toBe('/api/articles')
  })

  it('路径参数做编码（id 可能含斜杠/空格）', () => {
    expect(endpoints.articles.detail('a/b c')).toBe('/api/articles/a%2Fb%20c')
    expect(endpoints.articles.remove('x#y')).toBe('/api/articles/x%23y')
    expect(endpoints.feeds.remove('f 1')).toBe('/api/feeds/f%201')
    expect(endpoints.jobs.events('job-1')).toBe('/api/jobs/job-1/events')
    expect(endpoints.scheduler.runNow('fetch_job')).toBe('/api/scheduler/jobs/fetch_job/run-now')
  })

  it('日志流带 since 游标', () => {
    expect(endpoints.logs.events(0)).toBe('/api/logs/events?since=0')
    expect(endpoints.logs.events(42)).toBe('/api/logs/events?since=42')
    expect(endpoints.logs.list(7)).toBe('/api/logs?since=7')
  })

  it('目录浏览对绝对路径编码', () => {
    expect(endpoints.fs.list('/Users/me/Documents')).toBe(
      '/api/fs/list?path=%2FUsers%2Fme%2FDocuments',
    )
  })

  it('固定端点保持不变', () => {
    expect(endpoints.ping()).toBe('/api/ping')
    expect(endpoints.health()).toBe('/api/health')
    expect(endpoints.shutdown()).toBe('/api/shutdown')
    expect(endpoints.articles.bulkDelete()).toBe('/api/articles/bulk-delete')
    expect(endpoints.articles.retry()).toBe('/api/articles/retry')
    expect(endpoints.config.test()).toBe('/api/config/test')
    expect(endpoints.source.refresh()).toBe('/api/source/refresh')
    expect(endpoints.fs.pick()).toBe('/api/fs/pick')
  })

  it('连通性测试的 target 白名单与后端一致', () => {
    expect(endpoints.testTargets).toEqual(['llm', 'vision', 'source', 'all'])
  })
})
