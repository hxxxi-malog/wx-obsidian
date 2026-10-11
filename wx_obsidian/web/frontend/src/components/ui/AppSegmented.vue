<script setup lang="ts">
/** 分段控件：二到四个互斥选项（状态筛选、密度切换）。比下拉少一次点击。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: { label: string; value: string; count?: number }[]
    size?: 'sm' | 'md'
    disabled?: boolean
  }>(),
  { size: 'md', disabled: false },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()

const height = computed(() => (props.size === 'sm' ? 'h-7 text-[12.5px]' : 'h-8 text-[13px]'))
</script>

<template>
  <div
    class="inline-flex items-center gap-0.5 rounded-control border border-hairline bg-inset p-0.5"
    role="tablist"
  >
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      role="tab"
      :aria-selected="option.value === modelValue"
      :disabled="disabled"
      class="inline-flex items-center gap-1.5 rounded-[7px] px-2.5 font-medium transition-colors duration-150 disabled:opacity-50"
      :class="[
        height,
        option.value === modelValue
          ? 'bg-raised text-ink shadow-[var(--shadow-panel)]'
          : 'text-muted hover:text-ink',
      ]"
      @click="emit('update:modelValue', option.value)"
    >
      {{ option.label }}
      <span v-if="option.count !== undefined" class="nums font-mono text-[11px] text-faint">
        {{ option.count }}
      </span>
    </button>
  </div>
</template>
