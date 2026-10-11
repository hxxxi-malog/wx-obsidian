<script setup lang="ts">
/**
 * 任务坞：任何页面都能看到抓取进度（这是旧界面最大的体验缺口）。
 *
 * 折叠态是一个胶囊（进度环 + 计数），展开态是进度面板 + 阶段时间线 + 日志台。
 * 状态全部来自 jobs store（SSE），本组件不自己建连接，路由切换不重建。
 */
import { PhCaretDown, PhCheckCircle, PhSpinnerGap, PhX } from '@phosphor-icons/vue'
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import AppButton from '@/components/ui/AppButton.vue'
import AppProgress from '@/components/ui/AppProgress.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { useJobsStore } from '@/stores/jobs'
import { JOB_STAGE_TIMELINE, jobStageIndex, jobStateMeta, logLevelTone } from '@/lib/status'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (event: 'update:open', value: boolean): void }>()

const jobs = useJobsStore()
const router = useRouter()

const follow = ref(true)
const logBox = ref<HTMLElement | null>(null)

const stateMeta = computed(() => jobStateMeta(jobs.job?.state))
const stageIndex = computed(() => jobStageIndex(jobs.job?.stage))
const failedItems = computed(() => jobs.items.filter((item) => item.status !== 'done'))

const elapsedLabel = computed(() => {
  const total = jobs.elapsedMs
  if (!total) {
    return ''
  }
  const seconds = Math.floor(total / 1000)
  const minutes = Math.floor(seconds / 60)
  return minutes > 0 ? `${minutes} 分 ${String(seconds % 60).padStart(2, '0')} 秒` : `${seconds} 秒`
})

const circumference = 2 * Math.PI * 15
const dashOffset = computed(() => circumference * (1 - jobs.percent / 100))

async function scrollLogToEnd(): Promise<void> {
  if (!follow.value) {
    return
  }
  await nextTick()
  const box = logBox.value
  if (box) {
    box.scrollTop = box.scrollHeight
  }
}

watch(() => jobs.logLines.length, scrollLogToEnd)

function onLogScroll(): void {
  const box = logBox.value
  if (!box) {
    return
  }
  follow.value = box.scrollHeight - box.scrollTop - box.clientHeight < 24
}

function goRunPage(): void {
  emit('update:open', false)
  void router.push('/run')
}
</script>

<template>
  <div v-if="jobs.job" class="pointer-events-none fixed right-4 bottom-4 z-30 flex flex-col items-end gap-2">
    <!-- 展开面板 -->
    <Transition
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="opacity-0 translate-y-3"
      leave-active-class="transition duration-150 ease-in"
      leave-to-class="opacity-0 translate-y-2"
    >
      <section
        v-if="props.open"
        class="panel pointer-events-auto w-[min(440px,calc(100vw-32px))] overflow-hidden rounded-panel shadow-float"
        aria-label="任务进度"
      >
        <header class="flex items-center gap-2 border-b border-hairline px-4 py-3">
          <AppStatusDot
            :tone="stateMeta.tone === 'accent' ? 'accent' : stateMeta.tone === 'success' ? 'success' : stateMeta.tone === 'danger' ? 'danger' : 'neutral'"
            :pulse="jobs.running"
          />
          <span class="text-[13.5px] font-medium text-ink">{{ jobs.job.kind === 'retry' ? '重试任务' : '抓取任务' }}</span>
          <AppTag :tone="stateMeta.tone">{{ stateMeta.label }}</AppTag>
          <span v-if="elapsedLabel" class="nums font-mono text-[11.5px] text-faint">已用 {{ elapsedLabel }}</span>
          <button
            type="button"
            class="ml-auto grid size-7 place-items-center rounded-[8px] text-faint transition-colors hover:bg-raised hover:text-ink"
            aria-label="收起任务面板"
            @click="emit('update:open', false)"
          >
            <PhCaretDown :size="15" aria-hidden="true" />
          </button>
        </header>

        <div class="flex flex-col gap-3 px-4 py-3.5">
          <div class="flex items-baseline gap-2">
            <span class="nums font-mono text-[22px] leading-none font-semibold text-ink">
              {{ jobs.job.completed }}<span class="text-faint">/{{ jobs.job.total || '?' }}</span>
            </span>
            <span class="text-xs text-faint">{{ jobs.running ? '处理中' : '已结束' }}</span>
            <span v-if="jobs.connected" class="ml-auto text-[11px] text-faint">实时</span>
          </div>

          <AppProgress :value="jobs.percent" :indeterminate="jobs.running && !jobs.job.total" :tone="jobs.job.state === 'error' ? 'danger' : 'accent'" show-value />

          <!-- 阶段时间线 -->
          <ol class="m-0 flex list-none items-center gap-1 p-0">
            <li
              v-for="(stage, index) in JOB_STAGE_TIMELINE"
              :key="stage.key"
              class="flex flex-1 items-center gap-1.5"
            >
              <span
                class="grid size-4 shrink-0 place-items-center rounded-full border text-[9px]"
                :class="
                  jobs.running && index === stageIndex
                    ? 'border-accent bg-accent-soft text-accent'
                    : index < stageIndex || (!jobs.running && jobs.job.state === 'finished')
                      ? 'border-success/50 text-success'
                      : 'border-hairline text-faint'
                "
              >
                <PhCheckCircle v-if="index < stageIndex || (!jobs.running && jobs.job.state === 'finished')" :size="11" aria-hidden="true" />
                <PhSpinnerGap v-else-if="jobs.running && index === stageIndex" :size="10" class="animate-spin" aria-hidden="true" />
                <span v-else aria-hidden="true">·</span>
              </span>
              <span
                class="truncate text-[11px]"
                :class="jobs.running && index === stageIndex ? 'text-ink' : 'text-faint'"
              >
                {{ stage.label }}
              </span>
            </li>
          </ol>

          <p v-if="jobs.job.last_title" class="m-0 truncate text-[12.5px] text-muted">
            当前：{{ jobs.job.last_title }}
          </p>
          <p v-if="jobs.job.error" class="m-0 text-[12.5px] whitespace-pre-line text-danger">{{ jobs.job.error }}</p>

          <!-- 日志台 -->
          <div
            v-if="jobs.logLines.length"
            ref="logBox"
            class="max-h-[180px] overflow-y-auto rounded-[10px] border border-hairline bg-inset px-3 py-2"
            @scroll="onLogScroll"
          >
            <p
              v-for="line in jobs.logLines"
              :key="line.seq"
              class="m-0 font-mono text-[11.5px] leading-relaxed break-all"
              :class="
                logLevelTone(line.level) === 'danger'
                  ? 'text-danger'
                  : logLevelTone(line.level) === 'warning'
                    ? 'text-warning'
                    : 'text-muted'
              "
            >
              {{ line.text }}
            </p>
          </div>

          <div v-if="!jobs.running && jobs.items.length" class="flex flex-wrap items-center gap-2 text-[12px]">
            <AppTag tone="success">成功 {{ jobs.summary.done ?? 0 }}</AppTag>
            <AppTag :tone="failedItems.length ? 'danger' : 'neutral'">失败 {{ jobs.summary.failed ?? 0 }}</AppTag>
            <button
              v-if="failedItems.length"
              type="button"
              class="text-accent transition-opacity hover:opacity-80"
              @click="goRunPage"
            >
              查看失败明细 →
            </button>
          </div>
        </div>

        <footer class="flex items-center gap-2 border-t border-hairline bg-inset px-4 py-2.5">
          <AppButton size="sm" variant="outline" @click="goRunPage">去抓取页</AppButton>
          <AppButton v-if="jobs.running" size="sm" variant="danger" @click="jobs.cancel()">取消任务</AppButton>
          <AppButton v-else size="sm" variant="ghost" @click="jobs.clearLog()">清空日志</AppButton>
          <span class="ml-auto text-[11px] text-faint">{{ follow ? '自动跟随' : '已暂停跟随' }}</span>
        </footer>
      </section>
    </Transition>

    <!-- 折叠胶囊 -->
    <button
      v-if="!props.open"
      type="button"
      class="panel pointer-events-auto flex items-center gap-2.5 rounded-full py-2 pr-4 pl-2 transition-transform duration-200 active:scale-[0.98]"
      :aria-label="`任务进度 ${jobs.percent}%`"
      @click="emit('update:open', true)"
    >
      <span class="relative grid size-8 place-items-center">
        <svg viewBox="0 0 34 34" class="size-8 -rotate-90" aria-hidden="true">
          <circle cx="17" cy="17" r="15" fill="none" stroke="var(--border-hairline)" stroke-width="3" />
          <circle
            cx="17"
            cy="17"
            r="15"
            fill="none"
            :stroke="jobs.job.state === 'error' ? 'var(--danger)' : 'var(--accent)'"
            stroke-width="3"
            stroke-linecap="round"
            :stroke-dasharray="circumference"
            :stroke-dashoffset="dashOffset"
            class="transition-[stroke-dashoffset] duration-500 ease-out-expo"
          />
        </svg>
        <PhCheckCircle
          v-if="!jobs.running && jobs.job.state === 'finished'"
          :size="13"
          class="absolute text-success"
          aria-hidden="true"
        />
        <PhX v-else-if="!jobs.running" :size="13" class="absolute text-danger" aria-hidden="true" />
      </span>
      <span class="flex flex-col items-start">
        <span class="text-[12.5px] font-medium text-ink">
          {{ jobs.running ? '抓取中' : stateMeta.label }}
        </span>
        <span class="nums font-mono text-[11px] text-faint">
          {{ jobs.job.completed }}/{{ jobs.job.total || '?' }}
        </span>
      </span>
    </button>
  </div>
</template>
