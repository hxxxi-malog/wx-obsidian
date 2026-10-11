<script setup lang="ts">
/**
 * 页头：左标题+描述（左对齐，不居中），右操作区。
 * 所有页面都用它，避免每页各写一份标题样式。
 */
withDefaults(
  defineProps<{
    title: string
    description?: string
    /** 紧凑模式：用于分区内的次级标题 */
    compact?: boolean
  }>(),
  { description: '', compact: false },
)
</script>

<template>
  <header
    class="flex flex-wrap items-start justify-between gap-x-6 gap-y-3"
    :class="compact ? 'mb-3' : 'mb-6'"
  >
    <div class="min-w-0">
      <h1
        class="m-0 font-semibold tracking-tight text-ink"
        :class="compact ? 'text-[15px]' : 'text-[22px] leading-tight'"
      >
        {{ title }}
      </h1>
      <p v-if="description" class="m-0 mt-1.5 max-w-[70ch] text-[13px] leading-relaxed text-muted">
        {{ description }}
      </p>
      <slot name="meta" />
    </div>

    <div v-if="$slots.actions" class="flex flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </header>
</template>
