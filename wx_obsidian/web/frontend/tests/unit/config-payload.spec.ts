import { describe, expect, it } from 'vitest'

import type { ConfigSnapshot } from '@/lib/api/types'
import { buildConfigPayload, emptyForm, formFromSnapshot } from '@/stores/config'

const SNAPSHOT: ConfigSnapshot = {
  llm: {
    active: 'deepseek-flash',
    available: ['deepseek-flash', 'deepseek-pro'],
    providers: {},
    effective: { provider: 'deepseek-flash', base_url: 'https://api.deepseek.com', model: 'deepseek-chat', api_key: 'sk-****' },
    api_key: 'sk-****',
    max_tokens: 8000,
    temperature: 0.3,
  },
  vision: {
    enabled: true,
    active: 'deepseek-flash',
    available: ['deepseek-flash'],
    providers: {},
    effective: { provider: 'deepseek-flash', base_url: 'https://api.deepseek.com', model: 'deepseek-vl', api_key: 'sk-****' },
    api_key: 'sk-****',
    max_concurrency: 4,
    timeout: 60,
  },
  source: {
    type: 'we-mp-rss',
    base_url: 'http://127.0.0.1:8001',
    timeout: 15,
    token: 'ey****',
    auto_sync: true,
    sync_interval_minutes: 30,
  },
  obsidian: { vault_path: '/Users/me/Documents/vault', articles_dir: '公众号文章' },
  fetch: { max_days: 7, max_workers: 5, feed_limit: 20 },
  web: { port: 8765, open_browser: true },
}

describe('formFromSnapshot', () => {
  it('回填可编辑字段，密钥字段一律留空（留空=不修改）', () => {
    const form = formFromSnapshot(SNAPSHOT)
    expect(form.llmActive).toBe('deepseek-flash')
    expect(form.llmKey).toBe('')
    expect(form.visionKey).toBe('')
    expect(form.sourceToken).toBe('')
    expect(form.llmBaseUrl).toBe('https://api.deepseek.com')
    expect(form.vaultPath).toBe('/Users/me/Documents/vault')
    expect(form.maxDays).toBe(7)
    expect(form.webPort).toBe(8765)
    expect(form.webOpenBrowser).toBe(true)
    expect(form.sourceAutoSync).toBe(true)
    expect(form.sourceSyncInterval).toBe(30)
  })
})

describe('buildConfigPayload', () => {
  it('只发白名单里的键，动态渠道键按当前 active 展开', () => {
    const form = { ...formFromSnapshot(SNAPSHOT), llmBaseUrl: 'https://x', llmModel: 'm1' }
    const { patch, env } = buildConfigPayload(form)

    expect(patch['llm.active']).toBe('deepseek-flash')
    expect(patch['llm.providers.deepseek-flash.base_url']).toBe('https://x')
    expect(patch['llm.providers.deepseek-flash.model']).toBe('m1')
    expect(patch['vision.providers.deepseek-flash.model']).toBe('deepseek-vl')
    expect(patch['source.type']).toBe('we-mp-rss')
    expect(patch['source.auto_sync']).toBe(true)
    expect(patch['source.sync_interval_minutes']).toBe(30)
    expect(patch['obsidian.vault_path']).toBe('/Users/me/Documents/vault')
    expect(patch['fetch.max_days']).toBe(7)
    expect(patch['web.open_browser']).toBe(true)
    expect(env).toEqual({})
  })

  it('密钥留空则既不发 patch 也不发 env（否则会清掉已存的 Key）', () => {
    const { patch, env } = buildConfigPayload(formFromSnapshot(SNAPSHOT))
    expect(patch).not.toHaveProperty('source.token')
    expect(Object.keys(env)).toHaveLength(0)
  })

  it('填了密钥才带上对应的 env 变量名', () => {
    const form = { ...formFromSnapshot(SNAPSHOT), llmKey: 'sk-new', visionKey: 'sk-vis' }
    const { env } = buildConfigPayload(form)
    expect(env).toEqual({ DEEPSEEK_API_KEY: 'sk-new', VISION_API_KEY: 'sk-vis' })
  })

  it('令牌填了才发 source.token', () => {
    const form = { ...formFromSnapshot(SNAPSHOT), sourceToken: 'new-token' }
    expect(buildConfigPayload(form).patch['source.token']).toBe('new-token')
  })

  it('渠道名为空时不发 provider 子键（避免造出不存在的渠道）', () => {
    const form = { ...emptyForm(), llmActive: '', visionActive: '', vaultPath: '/v' }
    const { patch } = buildConfigPayload(form)
    expect(Object.keys(patch).some((key) => key.includes('.providers.'))).toBe(false)
    expect(patch).not.toHaveProperty('llm.active')
    expect(patch).not.toHaveProperty('vision.active')
  })

  it('环境变量键名与后端白名单一致（只有这两个界面可填）', () => {
    const form = { ...emptyForm(), llmKey: 'a', visionKey: 'b' }
    const keys = Object.keys(buildConfigPayload(form).env)
    expect(keys.sort()).toEqual(['DEEPSEEK_API_KEY', 'VISION_API_KEY'])
  })
})
