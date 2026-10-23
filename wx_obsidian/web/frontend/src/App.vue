<script setup lang="ts">
/**
 * 应用外壳：侧栏 + 顶栏 + 内容区 + 任务坞 + 命令面板 + 提示层。
 *
 * 这里承担三件事：
 *  1. 主题初始化（首屏定色在 index.html，这里接管切换与跟随系统）；
 *  2. 全局快捷键（⌘K / Ctrl+K 命令面板、Esc 关闭、「/」聚焦搜索）；
 *  3. 全局数据：数据源探活（顶栏胶囊）与任务状态（任务坞）——都放在最外层，
 *     切页面不会重建 SSE 连接，也不会重复请求。
 */
import { useEventListener } from '@vueuse/core'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import TokenGate from '@/components/domain/TokenGate.vue'
import AppRail from '@/components/layout/AppRail.vue'
import AppTopbar from '@/components/layout/AppTopbar.vue'
import CommandPalette from '@/components/layout/CommandPalette.vue'
import FeedSyncDock from '@/components/layout/FeedSyncDock.vue'
import JobDock from '@/components/layout/JobDock.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import Toaster from '@/components/ui/Toaster.vue'
import { usePolling } from '@/composables/usePolling'
import { useToast } from '@/composables/useToast'
import { apiGet, onUnauthorized } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { SourceHealth } from '@/lib/api/types'
import { useAuthStore } from '@/stores/auth'
import { useFeedSyncStore } from '@/stores/feed-sync'
import { useJobsStore } from '@/stores/jobs'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const jobs = useJobsStore()
const auth = useAuthStore()
const sync = useFeedSyncStore()
const toast = useToast()

const sourceHealth = ref<SourceHealth | null>(null)
const healthLoading = ref(false)
const navOpen = ref(false)
const notifiedJob = ref('')

const railWidth = computed(() => (ui.railCollapsed ? 'lg:w-[68px]' : 'lg:w-[232px]'))

async function probeSource(): Promise<void> {
  healthLoading.value = true
  try {
    sourceHealth.value = await apiGet<SourceHealth>(endpoints.source.health())
  } catch {
    sourceHealth.value = null
  } finally {
    healthLoading.value = false
  }
}

onMounted(() => {
  ui.applyTheme()
  // 刷新页面后重新挂上正在跑的任务（旧行为保留）
  void jobs.refresh()
  // 登录后触发的公众号更新是后台线程：刷新页面后把右下角进度卡接回来
  void sync.resume()
  // 裸地址/新标签页进来时没有 token：直接给解锁入口，用户不必对着一屏 401 提示自行摸索
  auth.promptIfNeeded()
})

// 任何接口的 401 都走同一条恢复路径（令牌缺失或服务重启后失效）
const stopAuthWatch = onUnauthorized(() => auth.markUnauthorized())

// 空闲时低频轮询：捕捉定时任务发起的抓取，让任务坞在任何页面都能自己冒出来
usePolling(
  async () => {
    if (!jobs.running) {
      await jobs.refresh()
    }
  },
  20000,
)

usePolling(probeSource, 45000, { immediate: true })

const stopWatchingTheme = ui.watchSystemTheme()
watch(() => ui.themeMode, () => ui.applyTheme())

// 任务结束（无论用户在哪个页面）都给一次明确反馈，并自动展开任务坞
watch(
  () => jobs.job?.state,
  (state, previous) => {
    const job = jobs.job
    if (!job || state === previous || !state) {
      return
    }
    if (state === 'running') {
      ui.openDock()
      return
    }
    if (notifiedJob.value === job.job_id) {
      return
    }
    notifiedJob.value = job.job_id
    if (state === 'finished') {
      const result = job.result ?? {}
      toast.success(
        `抓取完成：成功 ${result.done ?? 0} 篇`,
        result.failed ? `失败 ${result.failed} 篇，可在任务坞里看明细` : '全部成功',
        { label: '查看明细', run: () => ui.openDock() },
      )
    } else if (state === 'cancelled') {
      toast.warn('任务已取消', `已完成 ${job.result?.done ?? 0} 篇`)
    } else if (state === 'error') {
      toast.error(job.error || '任务失败', '详情见任务坞')
    }
  },
  { flush: 'post' },
)

useEventListener(window, 'keydown', (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null
  const typing =
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target?.isContentEditable === true

  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    ui.togglePalette()
    return
  }
  if (event.key === 'Escape' && !typing) {
    if (ui.paletteOpen) {
      ui.togglePalette(false)
    } else if (ui.dockOpen) {
      ui.closeDock()
    }
    return
  }
  // 「/」聚焦列表页的搜索框（在输入框里按 / 不劫持）
  if (event.key === '/' && !typing && !ui.paletteOpen) {
    const search = document.querySelector<HTMLInputElement>('[data-search-input]')
    if (search) {
      event.preventDefault()
      search.focus()
    }
  }
})

onBeforeUnmount(() => {
  stopWatchingTheme()
  stopAuthWatch()
})
</script>

<template>
  <div class="flex min-h-[100dvh] bg-page text-ink">
    <aside
      class="sticky top-0 hidden h-[100dvh] shrink-0 border-r border-hairline bg-panel transition-[width] duration-200 ease-out-expo lg:block"
      :class="railWidth"
    >
      <AppRail variant="rail" />
    </aside>

    <div class="flex min-w-0 flex-1 flex-col">
      <AppTopbar
        :source-health="sourceHealth"
        :health-loading="healthLoading"
        :dock-open="ui.dockOpen"
        @open-palette="ui.togglePalette(true)"
        @toggle-nav="navOpen = true"
        @toggle-dock="ui.toggleDock()"
      />

      <!-- 令牌闸门：未解锁时横幅常驻（任何页面都能一键粘贴 token 恢复） -->
      <TokenGate />

      <main class="mx-auto w-full max-w-[1400px] flex-1 px-4 py-5 sm:px-6 sm:py-6">
        <router-view v-slot="{ Component }">
          <Transition
            mode="out-in"
            enter-active-class="transition duration-200 ease-out"
            enter-from-class="opacity-0 translate-y-1"
            leave-active-class="transition duration-100 ease-in"
            leave-to-class="opacity-0"
          >
            <component :is="Component" />
          </Transition>
        </router-view>
      </main>
    </div>

    <AppDrawer v-model:open="navOpen" title="导航" width="md">
      <AppRail variant="drawer" @navigate="navOpen = false" />
    </AppDrawer>

    <JobDock :open="ui.dockOpen" @update:open="ui.toggleDock($event)" />
    <FeedSyncDock />
    <CommandPalette :open="ui.paletteOpen" @update:open="ui.togglePalette($event)" />
    <Toaster />
  </div>
</template>
