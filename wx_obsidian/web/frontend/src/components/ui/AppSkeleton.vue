<script setup lang="ts">
/**
 * 骨架屏：形状必须贴近真实布局（整块/多行/标题+正文），
 * 不允许出现「居中转圈」这种与内容无关的加载态。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    variant?: 'line' | 'block' | 'rows' | 'stat'
    rows?: number
    width?: string
    height?: string
  }>(),
  { variant: 'line', rows: 3, width: '', height: '' },
)

const lineStyle = computed(() => ({
  width: props.width || undefined,
  height: props.height || undefined,
}))
</script>

<template>
  <div v-if="variant === 'rows'" class="flex flex-col gap-2.5" role="status" aria-label="加载中">
    <div
      v-for="index in rows"
      :key="index"
      class="shimmer h-3.5 rounded-[6px]"
      :style="{ width: index === rows ? '62%' : '100%' }"
    />
  </div>

  <div v-else-if="variant === 'stat'" class="flex flex-col gap-2" role="status" aria-label="加载中">
    <div class="shimmer h-7 w-24 rounded-[8px]" />
    <div class="shimmer h-3 w-14 rounded-[6px]" />
  </div>

  <div
    v-else-if="variant === 'block'"
    class="shimmer rounded-panel"
    :style="{ height: height || '96px', width: width || '100%' }"
    role="status"
    aria-label="加载中"
  />

  <div
    v-else
    class="shimmer h-3.5 rounded-[6px]"
    :style="lineStyle"
    role="status"
    aria-label="加载中"
  />
</template>
