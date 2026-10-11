<script setup lang="ts">
/**
 * 日志台：运行日志与系统告警共用一套视图。
 *
 * 为什么两处共用：它们的形态完全一样（等宽、按级别着色、限高内部滚动、可复制），
 * 差别只在数据来源与工具栏开关——分成两个组件只会让两边的滚动跟随逻辑各坏一次。
 *
 * 交互要点：
 *  1. **自动跟随**：用户往上滚（离底部 > 24px）就自动暂停跟随，滚回底部自动恢复；
 *     也可以手动点「暂停跟随」把视图钉住，回头慢慢看。
 *  2. 限高由 `maxHeight` 决定，超出走内部滚动——绝不把页面整体撑高（无横向滚动）。
 *  3. 复制走 `AppCopyButton`，剪贴板被拒绝时如实提示，不假装成功。
 */
import { PhArrowDown, PhPause, PhPlay, PhTerminalWindow } from '@phosphor-icons/vue'
import { computed, nextTick, ref, watch } from 'vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppCopyButton from '@/components/ui/AppCopyButton.vue'
import { formatTime } from '@/lib/format'
import type { Tone } from '@/lib/status'
import { logLevelTone } from '@/lib/status'

/** 归一后的日志行：任务事件日志与系统日志都投到这个形状 */
export interface LogConsoleLine {
  seq: number
  /** ISO 时间戳 */
  ts: string
  /** INFO / WARNING / ERROR / CRITICAL … */
  level: string
  /** 来源（系统日志的 logger 名） */
  logger?: string
  text: string
}

const props = withDefaults(
  defineProps<{
    lines: LogConsoleLine[]
    /** 面板标题；留空则不渲染标题栏 */
    title?: string
    /** 一句话说明这栏日志是干什么的（「终端看不到的告警必须能在界面上看到」这类语义） */
    description?: string
    emptyText?: string
    maxHeight?: string
    /** 连接指示：true=实时 / false=已断开；不传则不显示 */
    connected?: boolean | null
    /** 关闭后不显示时间戳 */
    showTime?: boolean
    /** 关闭后没有清空/复制/跟随工具（纯只读的展示场景） */
    tools?: boolean
  }>(),
  {
    title: '',
    description: '',
    emptyText: '暂无日志',
    maxHeight: '320px',
    connected: null,
    showTime: true,
    tools: true,
  },
)

const emit = defineEmits<{ (event: 'clear'): void }>()

const box = ref<HTMLElement | null>(null)
/** 是否自动滚到底部（跟随最新一行） */
const follow = ref(true)
/** 手动钉住：不跟随，也不因滚动位置变化被自动打开 */
const pinned = ref(false)
const hasNewBelow = ref(false)

const followLabel = computed(() => (follow.value && !pinned.value ? '暂停跟随' : '跟随最新'))

const toneText: Record<Tone, string> = {
  neutral: 'text-muted',
  accent: 'text-accent',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  info: 'text-info',
}

const toneDot: Record<Tone, string> = {
  neutral: 'bg-faint',
  accent: 'bg-accent',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
}

const levelTone = (level: string): Tone => logLevelTone(level)

const plainText = computed(() =>
  props.lines
    .map((line) => {
      const stamp = props.showTime && line.ts ? `${formatTime(line.ts)} ` : ''
      const source = line.logger ? `${line.logger} ` : ''
      return `${stamp}[${line.level}] ${source}${line.text}`
    })
    .join('\n'),
)

async function scrollToEnd(): Promise<void> {
  await nextTick()
  const element = box.value
  if (element) {
    element.scrollTop = element.scrollHeight
  }
  hasNewBelow.value = false
}

function atBottom(element: HTMLElement): boolean {
  return element.scrollHeight - element.scrollTop - element.clientHeight < 24
}

function onScroll(): void {
  const element = box.value
  if (!element) {
    return
  }
  if (pinned.value) {
    return
  }
  follow.value = atBottom(element)
  if (follow.value) {
    hasNewBelow.value = false
  }
}

function toggleFollow(): void {
  if (follow.value || pinned.value) {
    follow.value = false
    pinned.value = true
    return
  }
  pinned.value = false
  follow.value = true
  void scrollToEnd()
}

function jumpToEnd(): void {
  pinned.value = false
  follow.value = true
  void scrollToEnd()
}

watch(
  () => props.lines.length,
  () => {
    if (follow.value) {
      void scrollToEnd()
      return
    }
    hasNewBelow.value = true
  },
)

// 首屏有内容时直接落到最新一行，而不是从头开始看
watch(
  box,
  (element) => {
    if (element && follow.value) {
      void scrollToEnd()
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex min-w-0 flex-col">
    <header
      v-if="title || description || tools || $slots.actions"
      class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 pb-3"
    >
      <div class="min-w-0">
        <div v-if="title" class="flex flex-wrap items-center gap-2">
          <h3 class="m-0 text-[13.5px] font-medium text-ink">{{ title }}</h3>
          <span v-if="connected !== null" class="inline-flex items-center gap-1.5 text-[11.5px]">
            <span
              class="inline-block size-1.5 shrink-0 rounded-full"
              :class="connected ? 'bg-success breathe' : 'bg-faint'"
              aria-hidden="true"
            />
            <span :class="connected ? 'text-success' : 'text-faint'">
              {{ connected ? '实时' : '已断开' }}
            </span>
          </span>
          <span class="nums font-mono text-[11px] text-faint">{{ lines.length }} 行</span>
        </div>
        <p v-if="description" class="m-0 mt-1 max-w-[80ch] text-[12px] leading-relaxed text-faint">
          {{ description }}
        </p>
      </div>

      <div class="flex flex-wrap items-center gap-1.5">
        <slot name="actions" />
        <AppButton v-if="tools" size="sm" variant="ghost" @click="toggleFollow">
          <template #icon>
            <PhPause v-if="follow && !pinned" :size="13" aria-hidden="true" />
            <PhPlay v-else :size="13" aria-hidden="true" />
          </template>
          {{ followLabel }}
        </AppButton>
        <AppCopyButton
          v-if="tools && lines.length"
          :value="plainText"
          label="复制全部"
          success-message="已复制全部日志"
        />
        <AppButton v-if="tools && lines.length" size="sm" variant="ghost" @click="emit('clear')">
          清空
        </AppButton>
      </div>
    </header>

    <div class="relative">
      <div
        ref="box"
        class="overflow-auto overscroll-contain rounded-panel border border-hairline bg-inset"
        :style="{ maxHeight }"
        role="log"
        aria-live="off"
        @scroll.passive="onScroll"
      >
        <ol v-if="lines.length" class="m-0 list-none p-0">
          <li
            v-for="line in lines"
            :key="line.seq"
            class="flex items-start gap-2.5 border-b border-hairline px-3 py-1.5 font-mono text-[12px] leading-relaxed last:border-b-0"
          >
            <span
              class="mt-[6px] inline-block size-1.5 shrink-0 rounded-full"
              :class="toneDot[levelTone(line.level)]"
              aria-hidden="true"
            />
            <span v-if="showTime" class="nums shrink-0 text-faint">{{ formatTime(line.ts) }}</span>
            <span class="w-[68px] shrink-0 font-medium" :class="toneText[levelTone(line.level)]">
              {{ line.level }}
            </span>
            <span v-if="line.logger" class="hidden shrink-0 text-faint sm:inline">{{
              line.logger
            }}</span>
            <span class="min-w-0 flex-1 break-words whitespace-pre-wrap text-ink">{{
              line.text
            }}</span>
          </li>
        </ol>

        <div v-else class="flex flex-col items-center gap-2 px-4 py-10 text-center">
          <PhTerminalWindow :size="20" class="text-faint" aria-hidden="true" />
          <p class="m-0 text-[12.5px] text-faint">{{ emptyText }}</p>
        </div>
      </div>

      <button
        v-if="hasNewBelow || pinned"
        type="button"
        class="panel absolute right-3 bottom-3 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] text-muted shadow-float transition-colors hover:text-ink"
        @click="jumpToEnd"
      >
        <PhArrowDown :size="12" aria-hidden="true" />
        {{ pinned ? '已暂停跟随' : '有新日志' }}
      </button>
    </div>
  </div>
</template>
