<script setup lang="ts">
/** 标签/徽标：状态、分类、计数都用它，避免每页各写一套配色。 */
import { computed } from 'vue'

import type { Tone } from '@/lib/status'

const props = withDefaults(
  defineProps<{
    tone?: Tone
    variant?: 'soft' | 'outline' | 'solid'
    /** 等宽字体（ID、计数、时间） */
    mono?: boolean
    size?: 'sm' | 'md'
  }>(),
  { tone: 'neutral', variant: 'soft', mono: false, size: 'sm' },
)

const TONES: Record<Tone, { soft: string; outline: string; solid: string }> = {
  neutral: {
    soft: 'bg-raised text-muted',
    outline: 'border border-hairline text-muted',
    solid: 'bg-raised text-ink',
  },
  accent: {
    soft: 'bg-accent-soft text-accent',
    outline: 'border border-accent/40 text-accent',
    solid: 'bg-accent text-accent-contrast',
  },
  success: {
    soft: 'bg-success-soft text-success',
    outline: 'border border-success/40 text-success',
    solid: 'bg-success text-success-contrast',
  },
  warning: {
    soft: 'bg-warning-soft text-warning',
    outline: 'border border-warning/40 text-warning',
    solid: 'bg-warning text-warning-contrast',
  },
  danger: {
    soft: 'bg-danger-soft text-danger',
    outline: 'border border-danger/40 text-danger',
    solid: 'bg-danger text-danger-contrast',
  },
  info: {
    soft: 'bg-info-soft text-info',
    outline: 'border border-info/40 text-info',
    solid: 'bg-info text-info-contrast',
  },
}

const classes = computed(() => [
  'inline-flex items-center gap-1 rounded-[6px] font-medium whitespace-nowrap',
  props.size === 'sm' ? 'h-[22px] px-2 text-[11.5px]' : 'h-7 px-2.5 text-xs',
  props.mono ? 'font-mono nums' : '',
  TONES[props.tone][props.variant],
])
</script>

<template>
  <span :class="classes"><slot /></span>
</template>
