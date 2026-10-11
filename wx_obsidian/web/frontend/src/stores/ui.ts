/**
 * UI 全局状态：主题、侧栏折叠、Toast、命令面板开关。
 *
 * 主题的**首屏**判定在 index.html 的内联脚本里（避免 FOUC），这里只负责后续切换、
 * 持久化与跟随系统。两处的 storage key 必须一致。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'
const THEME_KEY = 'wx-obsidian-theme'

export interface Toast {
  id: number
  tone: 'success' | 'warning' | 'danger' | 'info'
  title: string
  description?: string
  action?: { label: string; run: () => void }
  timeout: number
}

function readStoredTheme(): ThemeMode {
  const saved = localStorage.getItem(THEME_KEY)
  return saved === 'light' || saved === 'dark' ? saved : 'system'
}

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export const useUiStore = defineStore('ui', () => {
  const themeMode = ref<ThemeMode>(readStoredTheme())
  const railCollapsed = ref(localStorage.getItem('wx-obsidian-rail') === 'collapsed')
  const paletteOpen = ref(false)
  /** 任务坞展开状态放在 store 里：任何页面（例如抓取页的「查看当前任务」）都能打开它 */
  const dockOpen = ref(false)
  const toasts = ref<Toast[]>([])

  const isDark = computed(() =>
    themeMode.value === 'system' ? systemPrefersDark() : themeMode.value === 'dark',
  )

  /** 命令面板的触发源（App 顶部搜索框也用同一个状态） */
  function togglePalette(value?: boolean): void {
    paletteOpen.value = value ?? !paletteOpen.value
  }

  function toggleDock(value?: boolean): void {
    dockOpen.value = value ?? !dockOpen.value
  }

  function openDock(): void {
    dockOpen.value = true
  }

  function closeDock(): void {
    dockOpen.value = false
  }

  function applyTheme(): void {
    document.documentElement.classList.toggle('dark', isDark.value)
  }

  function setTheme(mode: ThemeMode): void {
    themeMode.value = mode
    if (mode === 'system') {
      localStorage.removeItem(THEME_KEY)
    } else {
      localStorage.setItem(THEME_KEY, mode)
    }
    applyTheme()
  }

  /** light → dark → system 三态轮转（顶栏按钮语义简单，不需要下拉） */
  function cycleTheme(): ThemeMode {
    const next: ThemeMode = themeMode.value === 'system' ? 'dark' : themeMode.value === 'dark' ? 'light' : 'system'
    setTheme(next)
    return next
  }

  function toggleRail(): void {
    railCollapsed.value = !railCollapsed.value
    localStorage.setItem('wx-obsidian-rail', railCollapsed.value ? 'collapsed' : 'expanded')
  }

  /** 跟随系统变化（只在 mode=system 时生效） */
  function watchSystemTheme(): () => void {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (): void => {
      if (themeMode.value === 'system') {
        applyTheme()
      }
    }
    media.addEventListener('change', handler)
    return () => media.removeEventListener('change', handler)
  }

  let toastSeq = 0

  function pushToast(toast: Omit<Toast, 'id' | 'timeout'> & { timeout?: number }): number {
    toastSeq += 1
    const id = toastSeq
    toasts.value = [...toasts.value.slice(-3), { ...toast, id, timeout: toast.timeout ?? 4200 }]
    if ((toast.timeout ?? 4200) > 0) {
      window.setTimeout(() => dismissToast(id), toast.timeout ?? 4200)
    }
    return id
  }

  function dismissToast(id: number): void {
    toasts.value = toasts.value.filter((item) => item.id !== id)
  }

  return {
    themeMode,
    isDark,
    railCollapsed,
    paletteOpen,
    dockOpen,
    toasts,
    applyTheme,
    setTheme,
    cycleTheme,
    watchSystemTheme,
    toggleRail,
    togglePalette,
    toggleDock,
    openDock,
    closeDock,
    pushToast,
    dismissToast,
  }
})
