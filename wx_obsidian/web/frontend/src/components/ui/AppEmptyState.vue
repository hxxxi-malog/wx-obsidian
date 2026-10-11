<script setup lang="ts">
/**
 * 空态：不只是「没数据」，要告诉用户怎么把数据弄出来。
 * 图标走 Phosphor（禁 emoji），标题一句话，说明可以给完整指引。
 */
import { computed, type Component } from 'vue'

const props = withDefaults(
  defineProps<{
    icon?: Component
    title: string
    description?: string
    compact?: boolean
  }>(),
  { icon: undefined, description: '', compact: false },
)

const padding = computed(() => (props.compact ? 'py-8' : 'py-14'))
</script>

<template>
  <div class="flex flex-col items-center justify-center text-center" :class="padding">
    <div
      v-if="icon"
      class="mb-4 flex size-11 items-center justify-center rounded-panel border border-hairline bg-raised text-faint"
    >
      <component :is="icon" :size="20" aria-hidden="true" />
    </div>
    <p class="m-0 text-[15px] font-medium text-ink">{{ title }}</p>
    <p v-if="description" class="m-0 mt-1.5 max-w-[46ch] text-[13px] leading-relaxed text-muted">
      {{ description }}
    </p>
    <slot name="hint" />
    <div class="mt-4 empty:mt-0"><slot name="action" /></div>
  </div>
</template>
