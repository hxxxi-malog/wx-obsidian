<script setup lang="ts">
/** 面板：抬升面 + 发丝边。不要用它套卡片套卡片——层级只靠间距和分隔线。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    padding?: 'none' | 'sm' | 'md' | 'lg'
    /** 内嵌样式：用于表格/日志这类需要自带滚动的区域 */
    inset?: boolean
    as?: 'div' | 'section' | 'article' | 'aside'
  }>(),
  { padding: 'md', inset: false, as: 'section' },
)

const PADDING: Record<string, string> = {
  none: 'p-0',
  sm: 'p-3',
  md: 'p-5',
  lg: 'p-6',
}

const classes = computed(() => [
  'panel rounded-panel',
  props.inset ? 'bg-inset' : '',
  PADDING[props.padding],
])
</script>

<template>
  <component :is="as" :class="classes">
    <slot />
  </component>
</template>
