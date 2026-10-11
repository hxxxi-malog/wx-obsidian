<script setup lang="ts">
/**
 * 任务进度面板（只读视图；取消按钮是唯一动作，由页面接上 store）。
 *
 * 数据全部由页面从 jobs store 传入：store 里的 SSE 归约、断线重连、终态关闭都已做好，
 * 这个组件不自己建 EventSource，也不自己算百分比——避免两处口径不一致。
 *
 * 展示要点：
 *  - `completed / total` 用大号等宽数字（一眼能看出跑到第几篇）；
 *  - 阶段时间线来自 `JOB_STAGE_TIMELINE`（只列后端真的会发的阶段，不编造流程）；
 *  - `job.error` 保留换行：后端给的多行指引不能被压成一行。
 */
import { PhCheckCircle, PhSpinnerGap, PhWarningCircle, PhXCircle } from '@phosphor-icons/vue'
import { computed } from 'vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppProgress from '@/components/ui/AppProgress.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import AppTag from '@/components/ui/AppTag.vue'
import type { JobSnapshot } from '@/lib/api/types'
import { formatDuration } from '@/lib/format'
import { JOB_STAGE_TIMELINE, jobStageIndex, jobStageLabel, jobStateMeta } from '@/lib/status'

const props = withDefaults(
  defineProps<{
    job: JobSnapshot
    /** SSE 连接是否在线（false = 已断开，数字可能不再更新） */
    connected: boolean
    /** 已用时长（毫秒），由 store 的 ticker 每秒推进 */
    elapsedMs: number
    /** 进度百分比（0-100） */
    percent: number
    running: boolean
    /** 首次复核尚未回来，显示骨架而不是空态 */
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{ (event: 'cancel'): void }>()

const stateMeta = computed(() => jobStateMeta(props.job.state))
const stageIndex = computed(() => jobStageIndex(props.job.stage))
const progressTone = computed<'accent' | 'success' | 'danger'>(() => {
  if (props.job.state === 'error') {
    return 'danger'
  }
  if (props.job.state === 'finished') {
    return 'success'
  }
  return 'accent'
})

/** 收尾状态（失败/取消）里当前阶段就是停下的那一步 */
const currentFailed = computed(() => props.job.state === 'error' || props.job.state === 'cancelled')

interface StageView {
  key: string
  label: string
  /** done=打勾 / current=高亮 / failed=停在 / pending=待执行 */
  state: 'done' | 'current' | 'failed' | 'pending'
}

const stages = computed<StageView[]>(() =>
  JOB_STAGE_TIMELINE.map((stage, index) => {
    if (!props.running && props.job.state === 'finished') {
      return { ...stage, state: 'done' as const }
    }
    if (index < stageIndex.value) {
      return { ...stage, state: 'done' as const }
    }
    if (index === stageIndex.value) {
      if (props.running) {
        return { ...stage, state: 'current' as const }
      }
      return { ...stage, state: currentFailed.value ? ('failed' as const) : ('done' as const) }
    }
    return { ...stage, state: 'pending' as const }
  }),
)

const stageHint = computed(() => {
  const total = JOB_STAGE_TIMELINE.length
  const index = Math.min(stageIndex.value + 1, total)
  return `第 ${index} / ${total} 步`
})

const elapsedLabel = computed(() => formatDuration(props.elapsedMs))
/** 后端给的是 stage 哨兵（knowledge_graph 这类），展示一律过一层中文映射 */
const stageText = computed(() => jobStageLabel(props.job.stage))
const doneCount = computed(() => props.job.result?.done ?? props.job.completed)
const failedCount = computed(() => props.job.result?.failed ?? 0)
const finished = computed(() => !props.running)
/** 一句话说清「在跑什么 / 跑成什么样」，避免模板里塞条件片段被空白规则咬 */
const countSummary = computed(() => {
  if (props.running) {
    return '正在处理'
  }
  if (!props.job.total) {
    return '本次处理'
  }
  return `本次处理 · 成功 ${doneCount.value} · 失败 ${failedCount.value}`
})
</script>

<template>
  <section class="panel rounded-panel p-5" aria-label="任务进度">
    <header class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div class="flex min-w-0 flex-wrap items-center gap-2">
        <AppStatusDot
          :tone="
            running
              ? 'accent'
              : stateMeta.tone === 'danger'
                ? 'danger'
                : stateMeta.tone === 'success'
                  ? 'success'
                  : 'info'
          "
          :pulse="running"
        />
        <h3 class="m-0 text-[13.5px] font-medium text-ink">
          {{ job.kind === 'retry' ? '重试任务' : '抓取任务' }}
        </h3>
        <AppTag :tone="stateMeta.tone">{{ stateMeta.label }}</AppTag>
        <span class="inline-flex items-center gap-1.5 text-[11.5px]">
          <span
            class="inline-block size-1.5 shrink-0 rounded-full"
            :class="connected ? 'bg-success breathe' : 'bg-faint'"
            aria-hidden="true"
          />
          <span :class="connected ? 'text-success' : 'text-faint'">
            {{ connected ? '实时' : '已断开' }}
          </span>
        </span>
      </div>

      <div class="flex flex-wrap items-center gap-1.5">
        <span class="nums font-mono text-[11.5px] text-faint">
          {{ stageText ? `当前阶段：${stageText}` : stageHint }}
        </span>
        <AppButton v-if="running" size="sm" variant="danger" @click="emit('cancel')">
          取消任务
        </AppButton>
      </div>
    </header>

    <div v-if="loading" class="mt-4 flex flex-col gap-3">
      <div class="shimmer h-9 w-40 rounded-[8px]" />
      <div class="shimmer h-1.5 w-full rounded-full" />
      <div class="shimmer h-6 w-full rounded-[8px]" />
    </div>

    <template v-else>
      <!-- 大号计数：本次跑完多少 / 一共多少 -->
      <div class="mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p class="m-0 flex items-baseline font-mono">
          <span class="nums text-[34px] leading-none font-semibold text-ink">
            {{ job.completed }}
          </span>
          <span class="nums px-1 text-[20px] leading-none text-faint">/</span>
          <span class="nums text-[20px] leading-none text-faint">{{ job.total || '?' }}</span>
        </p>
        <span class="text-[12px] text-faint">{{ countSummary }}</span>
        <span class="nums ml-auto font-mono text-[12px] text-muted">已用 {{ elapsedLabel }}</span>
      </div>

      <div class="mt-3">
        <AppProgress
          :value="percent"
          :indeterminate="running && !job.total"
          :tone="progressTone"
          show-value
        />
      </div>

      <!-- 阶段时间线 -->
      <ol class="m-0 mt-4 flex list-none flex-col gap-2 p-0 sm:flex-row sm:items-center sm:gap-3">
        <li v-for="stage in stages" :key="stage.key" class="flex min-w-0 flex-1 items-center gap-2">
          <span
            class="grid size-4 shrink-0 place-items-center rounded-full border"
            :class="
              stage.state === 'done'
                ? 'border-success/50 bg-success-soft text-success'
                : stage.state === 'current'
                  ? 'border-accent/50 bg-accent-soft text-accent'
                  : stage.state === 'failed'
                    ? 'border-danger/50 bg-danger-soft text-danger'
                    : 'border-hairline text-faint'
            "
            aria-hidden="true"
          >
            <PhCheckCircle v-if="stage.state === 'done'" :size="11" />
            <PhSpinnerGap v-else-if="stage.state === 'current'" :size="10" class="animate-spin" />
            <PhXCircle v-else-if="stage.state === 'failed'" :size="11" />
            <span v-else class="text-[9px] leading-none">·</span>
          </span>
          <span
            class="truncate text-[12px]"
            :class="
              stage.state === 'current'
                ? 'font-medium text-ink'
                : stage.state === 'failed'
                  ? 'text-danger'
                  : stage.state === 'done'
                    ? 'text-muted'
                    : 'text-faint'
            "
          >
            {{ stage.label }}
          </span>
        </li>
      </ol>

      <p
        v-if="job.last_title"
        class="m-0 mt-3 truncate text-[12.5px] text-muted"
        :title="job.last_title"
      >
        当前文章：{{ job.last_title }}
      </p>

      <p
        v-if="job.error"
        class="m-0 mt-3 flex items-start gap-2 rounded-control border border-danger/30 bg-danger-soft px-3 py-2 text-[12.5px] leading-relaxed text-ink"
      >
        <PhWarningCircle :size="15" class="mt-px shrink-0 text-danger" aria-hidden="true" />
        <span class="min-w-0 whitespace-pre-line break-words">{{ job.error }}</span>
      </p>

      <p
        v-if="finished && failedCount"
        class="m-0 mt-3 flex items-center gap-2 text-[12px] text-muted"
      >
        <PhWarningCircle :size="14" class="shrink-0 text-warning" aria-hidden="true" />
        有
        {{ failedCount }} 篇失败，可在「本次结果」里切到「只看失败」，或用下方「失败文章」逐篇重试。
      </p>
    </template>
  </section>
</template>
