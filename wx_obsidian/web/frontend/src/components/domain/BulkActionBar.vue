<script setup lang="ts">
/**
 * 批量操作条：表格上方的选中态工具条。
 *
 * 出现时机由视图决定（selected.length > 0），这里只做「已选 N 项 + 三个动作」。
 * 位置放在表格面板内部、表格之上：表格自己是一个 60dvh 的滚动区，所以滚动行时
 * 这条工具条始终留在视野里，不需要再叠 sticky（也就不会和粘性表头抢 z-index）。
 */
import { PhArrowsClockwise, PhTrash, PhX } from '@phosphor-icons/vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppIconButton from '@/components/ui/AppIconButton.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import { formatNumber } from '@/lib/format'

withDefaults(
  defineProps<{
    count: number
    /** 批量重试请求进行中 */
    retrying?: boolean
    /** 批量删除请求进行中 */
    deleting?: boolean
    /** 任一动作进行中：其余按钮一并禁用，避免并发写 processed.json */
    disabled?: boolean
  }>(),
  { retrying: false, deleting: false, disabled: false },
)

const emit = defineEmits<{
  (event: 'retry'): void
  (event: 'remove'): void
  (event: 'clear'): void
}>()
</script>

<template>
  <div
    class="rise flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-hairline bg-accent-soft px-3 py-2"
    role="region"
    aria-label="批量操作"
  >
    <p class="m-0 flex items-center gap-2 text-[13px] text-ink" aria-live="polite">
      <AppStatusDot tone="accent" />
      已选
      <span class="nums font-mono font-medium">{{ formatNumber(count) }}</span>
      项
    </p>

    <div class="ml-auto flex flex-wrap items-center gap-2">
      <AppButton
        size="sm"
        variant="outline"
        :loading="retrying"
        :disabled="disabled"
        @click="emit('retry')"
      >
        <template #icon>
          <PhArrowsClockwise :size="14" aria-hidden="true" />
        </template>
        批量重试
      </AppButton>

      <AppButton
        size="sm"
        variant="danger"
        :loading="deleting"
        :disabled="disabled"
        @click="emit('remove')"
      >
        <template #icon>
          <PhTrash :size="14" aria-hidden="true" />
        </template>
        批量删除
      </AppButton>

      <AppIconButton label="取消选择" size="sm" :disabled="disabled" @click="emit('clear')">
        <PhX :size="14" aria-hidden="true" />
      </AppIconButton>
    </div>
  </div>
</template>
