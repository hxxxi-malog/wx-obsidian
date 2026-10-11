<script setup lang="ts">
/**
 * 顶栏：面包屑 + 全局搜索入口 + 数据源状态 + 任务胶囊 + 主题切换。
 * 玻璃层（glass）：滚动时内容从下方穿过要有物理感，但要 1px 内缘折射而不是外发光。
 */
import {
  PhCaretRight,
  PhCommand,
  PhMagnifyingGlass,
  PhMonitor,
  PhMoon,
  PhSidebarSimple,
  PhSun,
} from '@phosphor-icons/vue'
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import AppKbd from '@/components/ui/AppKbd.vue'
import type { SourceHealth } from '@/lib/api/types'
import { NAV_GROUPS, navItemByKey } from '@/lib/navigation'
import { useJobsStore } from '@/stores/jobs'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{ sourceHealth: SourceHealth | null; healthLoading: boolean; dockOpen: boolean }>()
const emit = defineEmits<{
  (event: 'openPalette'): void
  (event: 'toggleNav'): void
  (event: 'toggleDock'): void
}>()

const ui = useUiStore()
const jobs = useJobsStore()
const route = useRoute()

const navKey = computed(() => (route.meta.navKey as string | undefined) ?? '')
const currentItem = computed(() => navItemByKey(navKey.value))
const groupName = computed(
  () => NAV_GROUPS.find((group) => group.items.some((item) => item.key === navKey.value))?.label ?? '系统',
)

const jobBadge = computed(() => {
  const job = jobs.job
  if (!job) {
    return ''
  }
  return job.total ? `${job.completed}/${job.total}` : `${job.completed}`
})

const themeIcon = computed(() => {
  if (ui.themeMode === 'light') {
    return PhSun
  }
  if (ui.themeMode === 'dark') {
    return PhMoon
  }
  return PhMonitor
})

const themeLabel = computed(() => {
  if (ui.themeMode === 'light') {
    return '浅色（点击切到深色）'
  }
  if (ui.themeMode === 'dark') {
    return '深色（点击跟随系统）'
  }
  return '跟随系统（点击切到浅色）'
})

const sourceTone = computed(() => {
  if (props.healthLoading && !props.sourceHealth) {
    return 'neutral' as const
  }
  return props.sourceHealth?.healthy ? ('success' as const) : ('danger' as const)
})

const sourceLabel = computed(() => {
  if (!props.sourceHealth) {
    return props.healthLoading ? '探测中…' : '数据源未知'
  }
  return props.sourceHealth.healthy
    ? `数据源正常 · ${props.sourceHealth.base_url}`
    : `数据源不可达 · ${props.sourceHealth.base_url}`
})
</script>

<template>
  <header class="glass sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-hairline px-3 sm:px-4">
    <button
      type="button"
      class="grid size-8 shrink-0 place-items-center rounded-[9px] text-muted transition-colors hover:bg-raised hover:text-ink lg:hidden"
      aria-label="打开导航"
      @click="emit('toggleNav')"
    >
      <PhSidebarSimple :size="17" aria-hidden="true" />
    </button>

    <div class="flex min-w-0 items-center gap-2">
      <span class="hidden truncate text-[12.5px] text-faint sm:block">{{ groupName }}</span>
      <PhCaretRight :size="12" class="hidden text-faint sm:block" aria-hidden="true" />
      <span class="truncate text-[13.5px] font-medium text-ink">
        {{ currentItem?.label ?? 'wx-obsidian' }}
      </span>
    </div>

    <div class="ml-auto flex items-center gap-1.5">
      <button
        type="button"
        class="hidden h-8 items-center gap-2 rounded-control border border-hairline bg-inset px-2.5 text-[12.5px] text-faint transition-colors hover:border-strong hover:text-muted md:flex"
        @click="emit('openPalette')"
      >
        <PhMagnifyingGlass :size="14" aria-hidden="true" />
        <span>搜索 / 跳转</span>
        <span class="ml-3 flex items-center gap-0.5">
          <AppKbd keys="⌘" />
          <AppKbd keys="K" />
        </span>
      </button>
      <button
        type="button"
        class="grid size-8 place-items-center rounded-[9px] text-muted transition-colors hover:bg-raised hover:text-ink md:hidden"
        aria-label="搜索与跳转"
        @click="emit('openPalette')"
      >
        <PhCommand :size="16" aria-hidden="true" />
      </button>

      <AppTooltip :content="sourceLabel" side="bottom">
        <router-link
          to="/source"
          class="hidden h-8 items-center gap-2 rounded-control border border-hairline px-2.5 text-[12.5px] text-muted transition-colors hover:border-strong hover:text-ink sm:flex"
        >
          <AppStatusDot :tone="sourceTone" :pulse="healthLoading && !sourceHealth" />
          <span class="hidden lg:inline">数据源</span>
        </router-link>
      </AppTooltip>

      <button
        v-if="jobs.job"
        type="button"
        class="flex h-8 items-center gap-2 rounded-control border px-2.5 text-[12.5px] transition-colors"
        :class="
          jobs.running
            ? 'border-accent/40 bg-accent-soft text-accent'
            : jobs.job.state === 'error'
              ? 'border-danger/35 bg-danger-soft text-danger'
              : 'border-hairline text-muted hover:border-strong hover:text-ink'
        "
        :aria-expanded="dockOpen"
        @click="emit('toggleDock')"
      >
        <AppStatusDot :tone="jobs.running ? 'accent' : jobs.job.state === 'error' ? 'danger' : 'success'" :pulse="jobs.running" />
        <span class="nums font-mono text-[11.5px]">{{ jobBadge }}</span>
        <span class="hidden sm:inline">{{ jobs.running ? '抓取中' : '查看结果' }}</span>
      </button>

      <AppTooltip :content="themeLabel" side="bottom">
        <button
          type="button"
          class="grid size-8 place-items-center rounded-[9px] text-muted transition-colors hover:bg-raised hover:text-ink"
          aria-label="切换主题"
          @click="ui.cycleTheme()"
        >
          <component :is="themeIcon" :size="16" aria-hidden="true" />
        </button>
      </AppTooltip>
    </div>
  </header>
</template>
