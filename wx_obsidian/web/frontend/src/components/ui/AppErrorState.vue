<script setup lang="ts">
/**
 * 错误态：就地展示 + 可重试。
 * 说明文本保留换行（后端有些错误是多行可执行指引，不能截断）。
 */
import { PhWarningCircle } from '@phosphor-icons/vue'

import AppButton from './AppButton.vue'

withDefaults(
  defineProps<{
    message: string
    /** 可选的补充说明（例如「数据源地址改动需重启后生效」） */
    hint?: string
    retryLabel?: string
    compact?: boolean
  }>(),
  { hint: '', retryLabel: '重试', compact: false },
)

defineEmits<{ (event: 'retry'): void }>()
</script>

<template>
  <div
    class="flex items-start gap-3 rounded-panel border border-danger/30 bg-danger-soft"
    :class="compact ? 'px-3 py-2.5' : 'px-4 py-3.5'"
    role="alert"
  >
    <PhWarningCircle :size="18" class="mt-px shrink-0 text-danger" aria-hidden="true" />
    <div class="min-w-0 flex-1">
      <p class="m-0 text-[13.5px] leading-relaxed whitespace-pre-line text-ink">{{ message }}</p>
      <p v-if="hint" class="m-0 mt-1 text-xs leading-relaxed whitespace-pre-line text-muted">
        {{ hint }}
      </p>
    </div>
    <AppButton v-if="$attrs.onRetry || retryLabel" size="sm" variant="outline" @click="$emit('retry')">
      {{ retryLabel }}
    </AppButton>
  </div>
</template>
