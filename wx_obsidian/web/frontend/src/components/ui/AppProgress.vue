<script setup lang="ts">
/** 进度条：确定态走宽度，不确定态走反光扫过（只动 transform/opacity）。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    value?: number
    indeterminate?: boolean
    tone?: 'accent' | 'success' | 'danger'
    size?: 'sm' | 'md'
    showValue?: boolean
  }>(),
  { value: 0, indeterminate: false, tone: 'accent', size: 'md', showValue: false },
)

const TONES: Record<string, string> = {
  accent: 'bg-accent',
  success: 'bg-success',
  danger: 'bg-danger',
}

const clamped = computed(() => Math.max(0, Math.min(100, props.value)))
const height = computed(() => (props.size === 'sm' ? 'h-1' : 'h-1.5'))
</script>

<template>
  <div class="flex items-center gap-3">
    <div
      class="relative w-full overflow-hidden rounded-full bg-raised"
      :class="height"
      role="progressbar"
      :aria-valuenow="indeterminate ? undefined : clamped"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div
        v-if="!indeterminate"
        class="h-full rounded-full transition-[width] duration-500 ease-out-expo"
        :class="TONES[tone]"
        :style="{ width: `${clamped}%` }"
      />
      <div v-else class="shimmer h-full w-full rounded-full" />
    </div>
    <span v-if="showValue && !indeterminate" class="nums w-10 text-right text-xs text-faint">
      {{ clamped }}%
    </span>
  </div>
</template>
