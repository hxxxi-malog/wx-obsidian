/**
 * 鉴权状态机：missing / rejected 两种未授权，各自对应的界面行为。
 *
 * 「只自动弹一次」是刻意的：401 会由多个并发请求同时触发，
 * 反复弹窗会把对话框变成打不掉的弹窗雨。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'

const TOKEN_KEY = 'wx-obsidian-token'

describe('鉴权状态（解锁闸门）', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
  })

  it('没有 token 时是 missing，首次自动弹窗，之后不再打断', () => {
    const auth = useAuthStore()
    expect(auth.status).toBe('missing')
    expect(auth.needsToken).toBe(true)

    auth.promptIfNeeded()
    expect(auth.dialogOpen).toBe(true)

    auth.closeDialog()
    auth.promptIfNeeded()
    expect(auth.dialogOpen).toBe(false)
  })

  it('有 token 时 401 归为 rejected（服务重启换了随机 token）', () => {
    sessionStorage.setItem(TOKEN_KEY, 'stale')
    const auth = useAuthStore()
    expect(auth.status).toBe('ok')
    expect(auth.needsToken).toBe(false)

    auth.markUnauthorized()
    expect(auth.status).toBe('rejected')
    expect(auth.dialogOpen).toBe(true)
  })

  it('unlock 写回 token、恢复 ok 并关闭对话框', () => {
    const auth = useAuthStore()
    auth.openDialog()
    auth.unlock('  fresh-token  ')
    expect(auth.status).toBe('ok')
    expect(auth.needsToken).toBe(false)
    expect(auth.dialogOpen).toBe(false)
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('fresh-token')
  })
})
