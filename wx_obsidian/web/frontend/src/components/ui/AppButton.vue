<script setup lang="ts">
/**
 * 按钮：五种语义变体 + 三档尺寸。
 * 触感：按下时下沉 1px（`:active`），不是只换颜色。
 */
import { PhCircleNotch } from '@phosphor-icons/vue'
import { computed } from 'vue'

type Variant = 'solid' | 'tonal' | 'outline' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    variant?: Variant
    size?: Size
    type?: 'button' | 'submit' | 'reset'
    loading?: boolean
    disabled?: boolean
    block?: boolean
  }>(),
  {
    variant: 'ghost',
    size: 'md',
    type: 'button',
    loading: false,
    disabled: false,
    block: false,
  },
)

const VARIANTS: Record<Variant, string> = {
  solid: 'bg-accent text-accent-contrast hover:bg-accent-hover shadow-[var(--shadow-panel)]',
  tonal: 'bg-accent-soft text-accent hover:brightness-105',
  outline: 'border border-strong text-ink hover:bg-raised',
  ghost: 'text-muted hover:bg-raised hover:text-ink',
  danger: 'bg-danger text-danger-contrast hover:brightness-110',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5 rounded-[8px]',
  md: 'h-9 px-3.5 text-[13.5px] gap-2',
  lg: 'h-11 px-5 text-[15px] gap-2',
}

const classes = computed(() => [
  'inline-flex shrink-0 items-center justify-center rounded-control font-medium whitespace-nowrap',
  'transition-[background-color,color,filter,transform] duration-150 ease-out-expo',
  'active:translate-y-[1px]',
  'disabled:pointer-events-none disabled:opacity-45',
  VARIANTS[props.variant],
  SIZES[props.size],
  props.block ? 'w-full' : '',
])
</script>

<template>
  <button
    :type="type"
    :class="classes"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <PhCircleNotch v-if="loading" :size="15" class="animate-spin" aria-hidden="true" />
    <slot v-else name="icon" />
    <slot />
  </button>
</template>
