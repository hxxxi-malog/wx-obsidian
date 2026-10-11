<script setup lang="ts">
/**
 * 指标条：**无盒子**，只靠 `divide-x` 发丝线分组。
 *
 * 为什么不做成卡片：三等分/四等分的「统计卡片行」是最典型的模板味（AI slop），
 * 而且每块内容都套盒子会让层级失效——这里数字够大、够齐（等宽 + tabular），
 * 分隔交给 1px 发丝线，页面才不会变成九宫格。
 *
 * 值由调用方格式化（口径属于页面语义，组件不猜）；组件只负责排版与色调。
 */
import { computed } from 'vue'

import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import type { Tone } from '@/lib/status'

export interface MetricItem {
  key: string
  label: string
  /** 已格式化好的值（千分位等由调用方决定） */
  value: string
  tone?: Tone
  /** 悬浮显示的口径说明（例如「仅统计 status=done」） */
  hint?: string
}

const props = withDefaults(
  defineProps<{
    items: MetricItem[]
    /** 首屏加载中：渲染与真实布局同形的骨架（4 格） */
    loading?: boolean
  }>(),
  { loading: false },
)

const TONES: Record<Tone, string> = {
  neutral: 'text-ink',
  accent: 'text-accent',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  info: 'text-info',
}

/** 骨架占位：指标条固定四项，加载时保持同样的格数与间距 */
const PLACEHOLDERS: MetricItem[] = Array.from({ length: 4 }, (_, index) => ({
  key: `metric-placeholder-${index}`,
  label: '',
  value: '',
}))

const cells = computed(() => (props.loading ? PLACEHOLDERS : props.items))
</script>

<template>
  <div class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between sm:gap-8">
    <div
      class="grid min-w-0 grid-cols-2 gap-x-5 gap-y-5 sm:flex sm:gap-0 sm:divide-x sm:divide-hairline"
    >
      <div
        v-for="cell in cells"
        :key="cell.key"
        class="min-w-0 sm:px-5 sm:first:pl-0 sm:last:pr-0"
      >
        <AppSkeleton v-if="loading" variant="stat" />

        <template v-else>
          <AppTooltip :content="cell.hint ?? ''" :disabled="!cell.hint">
            <p
              class="m-0 font-mono nums text-[24px] leading-none font-semibold sm:text-[27px]"
              :class="TONES[cell.tone ?? 'neutral']"
            >
              {{ cell.value }}
            </p>
          </AppTooltip>
          <p class="m-0 mt-2 text-[11.5px] text-faint">{{ cell.label }}</p>
        </template>
      </div>
    </div>

    <div
      v-if="$slots.meta"
      class="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-faint sm:pb-1"
    >
      <slot name="meta" />
    </div>
  </div>
</template>
