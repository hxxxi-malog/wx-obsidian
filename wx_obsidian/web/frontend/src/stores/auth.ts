/**
 * 访问令牌状态：缺 token / token 失效时，界面给出横幅 + 解锁对话框。
 *
 * token 本体仍由 `lib/api/client` 独占管理（sessionStorage 是唯一事实来源），
 * 这里只保存「界面该不该提示」。集中一处的好处：401 的处理不必散落到每个页面，
 * 任何接口（含轮询、任务 SSE）撞上 401 都会走到同一条恢复路径。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { hasToken, setToken } from '@/lib/api/client'

export type AuthStatus = 'ok' | 'missing' | 'rejected'

export const useAuthStore = defineStore('auth', () => {
  /** missing = 本标签页压根没有 token；rejected = 有 token 但服务不认（多半是服务重启换了随机 token） */
  const status = ref<AuthStatus>(hasToken() ? 'ok' : 'missing')
  const dialogOpen = ref(false)
  /** 每次页面加载只自动弹一次：点「稍后」之后不再反复打断，但横幅一直留着 */
  let autoOpened = false

  const needsToken = computed(() => status.value !== 'ok')

  /** 首次自动弹窗（App 挂载时调用）。已经弹过或已有对话框打开时不动。 */
  function promptIfNeeded(): void {
    if (!needsToken.value || autoOpened || dialogOpen.value) {
      return
    }
    autoOpened = true
    dialogOpen.value = true
  }

  /** 收到 401：按「有没有 token」区分缺失与失效 */
  function markUnauthorized(): void {
    status.value = hasToken() ? 'rejected' : 'missing'
    promptIfNeeded()
  }

  function openDialog(): void {
    dialogOpen.value = true
  }

  function closeDialog(): void {
    dialogOpen.value = false
  }

  /** 记住 token 并标记已解锁；调用方负责刷新页面，让所有请求重来 */
  function unlock(token: string): void {
    setToken(token)
    status.value = 'ok'
    dialogOpen.value = false
  }

  return {
    status,
    dialogOpen,
    needsToken,
    promptIfNeeded,
    markUnauthorized,
    openDialog,
    closeDialog,
    unlock,
  }
})
