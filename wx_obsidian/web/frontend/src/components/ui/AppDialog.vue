<script setup lang="ts">
/**
 * 模态对话框：Reka Dialog（焦点陷阱、Esc、aria）。
 * 视觉：遮罩 + 抬升面板 + 入场位移；不做外发光。
 */
import { PhX } from '@phosphor-icons/vue'
import { DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'

import AppIconButton from './AppIconButton.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    width?: 'sm' | 'md' | 'lg'
  }>(),
  { description: '', width: 'md' },
)

const emit = defineEmits<{ (event: 'update:open', value: boolean): void }>()

const WIDTHS: Record<string, string> = {
  sm: 'max-w-[420px]',
  md: 'max-w-[560px]',
  lg: 'max-w-[760px]',
}
</script>

<template>
  <DialogRoot :open="props.open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay
        class="fixed inset-0 z-40 bg-overlay data-[state=closed]:opacity-0 data-[state=open]:opacity-100 motion-safe:transition-opacity motion-safe:duration-200"
      />
      <DialogContent
        class="panel fixed top-1/2 left-1/2 z-50 flex max-h-[86dvh] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-panel shadow-float"
        :class="WIDTHS[width]"
      >
        <header class="flex items-start justify-between gap-4 border-b border-hairline px-5 py-4">
          <div class="min-w-0">
            <DialogTitle class="m-0 text-[15px] font-semibold text-ink">{{ title }}</DialogTitle>
            <DialogDescription v-if="description" class="m-0 mt-1 text-xs text-muted">
              {{ description }}
            </DialogDescription>
          </div>
          <AppIconButton label="关闭" size="sm" @click="emit('update:open', false)">
            <PhX :size="15" aria-hidden="true" />
          </AppIconButton>
        </header>

        <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <slot />
        </div>

        <footer
          v-if="$slots.footer"
          class="flex items-center justify-end gap-2 border-t border-hairline bg-inset px-5 py-3"
        >
          <slot name="footer" />
        </footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
