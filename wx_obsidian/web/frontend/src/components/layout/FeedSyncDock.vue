<script setup lang="ts">
/**
 * 公众号更新进度卡（右下角）。
 *
 * 扫码登录成功后后端在后台逐个触发采集，这里只负责展示，不阻塞任何操作：
 * 用户可以继续翻页、刷新列表，进度卡会自己走完并收起。
 * 与任务坞（JobDock）同处右下角，任务坞出现时整体上移，避免互相遮挡。
 */
import { PhArrowsClockwise, PhCheckCircle, PhWarningCircle, PhX } from '@phosphor-icons/vue'
import { computed } from 'vue'

import AppProgress from '@/components/ui/AppProgress.vue'
import { feedSyncView } from '@/lib/feed-sync'
import { useFeedSyncStore } from '@/stores/feed-sync'
import { useJobsStore } from '@/stores/jobs'

const sync = useFeedSyncStore()
const jobs = useJobsStore()

const view = computed(() => feedSyncView(sync.data, sync.error))
</script>

<template>
  <div
    v-if="sync.visible"
    class="pointer-events-none fixed right-4 z-30 w-[min(340px,calc(100vw-32px))]"
    :class="jobs.job ? 'bottom-24' : 'bottom-4'"
  >
    <section
      class="panel pointer-events-auto rounded-panel px-4 py-3 shadow-float"
      role="status"
      aria-live="polite"
      aria-label="公众号更新进度"
    >
      <div class="flex items-start gap-2">
        <PhArrowsClockwise
          v-if="view.running"
          :size="15"
          class="mt-0.5 shrink-0 animate-spin text-accent"
          aria-hidden="true"
        />
        <PhWarningCircle
          v-else-if="view.tone === 'warning'"
          :size="15"
          class="mt-0.5 shrink-0 text-warning"
          aria-hidden="true"
        />
        <PhCheckCircle
          v-else
          :size="15"
          class="mt-0.5 shrink-0 text-success"
          aria-hidden="true"
        />
        <p class="m-0 min-w-0 flex-1 text-[13px] leading-snug font-medium text-ink">
          {{ view.title }}
        </p>
        <button
          type="button"
          class="grid size-6 shrink-0 place-items-center rounded-[7px] text-faint transition-colors hover:bg-raised hover:text-ink"
          aria-label="关闭更新进度"
          @click="sync.dismiss()"
        >
          <PhX :size="13" aria-hidden="true" />
        </button>
      </div>

      <AppProgress
        class="mt-2.5"
        :value="view.percent"
        :tone="view.running ? 'accent' : view.tone === 'warning' ? 'danger' : 'success'"
        size="sm"
      />

      <p class="m-0 mt-2 text-[11.5px] leading-relaxed text-muted">{{ view.detail }}</p>
    </section>
  </div>
</template>
