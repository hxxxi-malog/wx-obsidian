<script setup lang="ts">
/**
 * 状态点：跑动中会「呼吸」，静止时只是一个点。
 * 永续动画只改 opacity/transform，且受 prefers-reduced-motion 全局关闭。
 */
import { computed } from 'vue'

import type { Tone } from '@/lib/status'

const props = withDefaults(
  defineProps<{
    tone?: Tone
    pulse?: boolean
    size?: 6 | 8 | 10
    label?: string
  }>(),
  { tone: 'neutral', pulse: false, size: 8, label: '' },
)

const TONES: Record<Tone, string> = {
  neutral: 'bg-faint',
  accent: 'bg-accent',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
}

const classes = computed(() => [
  'inline-block shrink-0 rounded-full',
  TONES[props.tone],
  props.pulse ? 'breathe' : '',
])

const style = computed(() => ({ width: `${props.size}px`, height: `${props.size}px` }))
</script>

<template>
  <span
    :class="classes"
    :style="style"
    :role="label ? 'img' : undefined"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : true"
  />
</template>
