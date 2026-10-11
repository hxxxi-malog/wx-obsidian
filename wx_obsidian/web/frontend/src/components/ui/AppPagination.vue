<script setup lang="ts">
/** 分页：左「第 x–y 条 / 共 n 条」，右页码 + 每页条数。 */
import { PhCaretLeft, PhCaretRight } from '@phosphor-icons/vue'
import { computed } from 'vue'

import { formatNumber } from '@/lib/format'
import AppIconButton from './AppIconButton.vue'
import AppSelect from './AppSelect.vue'

const props = withDefaults(
  defineProps<{
    page: number
    pageSize: number
    total: number
    pageSizes?: number[]
    disabled?: boolean
  }>(),
  { pageSizes: () => [20, 50, 100], disabled: false },
)

const emit = defineEmits<{
  (event: 'update:page', value: number): void
  (event: 'update:pageSize', value: number): void
}>()

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.pageSize))))
const from = computed(() => (props.total === 0 ? 0 : (props.page - 1) * props.pageSize + 1))
const to = computed(() => Math.min(props.total, props.page * props.pageSize))
const sizeOptions = computed(() => props.pageSizes.map((size) => ({ label: `${size} / 页`, value: String(size) })))

function go(target: number): void {
  const next = Math.min(pageCount.value, Math.max(1, target))
  if (next !== props.page) {
    emit('update:page', next)
  }
}
</script>

<template>
  <nav class="flex flex-wrap items-center justify-between gap-3 px-3 py-3" aria-label="分页">
    <p class="nums m-0 text-xs text-faint">
      <template v-if="total > 0">
        第 {{ from }}–{{ to }} 条 · 共 {{ formatNumber(total) }} 条
      </template>
      <template v-else>暂无数据</template>
    </p>

    <div class="flex items-center gap-2">
      <div class="w-[104px]">
        <AppSelect
          :model-value="String(pageSize)"
          :options="sizeOptions"
          size="sm"
          :disabled="disabled"
          @update:model-value="emit('update:pageSize', Number($event))"
        />
      </div>

      <div class="flex items-center gap-1">
        <AppIconButton label="上一页" size="sm" :disabled="disabled || page <= 1" @click="go(page - 1)">
          <PhCaretLeft :size="14" aria-hidden="true" />
        </AppIconButton>
        <span class="nums px-1.5 font-mono text-xs text-muted">{{ page }} / {{ pageCount }}</span>
        <AppIconButton
          label="下一页"
          size="sm"
          :disabled="disabled || page >= pageCount"
          @click="go(page + 1)"
        >
          <PhCaretRight :size="14" aria-hidden="true" />
        </AppIconButton>
      </div>
    </div>
  </nav>
</template>
