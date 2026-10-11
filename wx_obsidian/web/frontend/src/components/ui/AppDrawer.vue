<script setup lang="ts">
/** 右侧抽屉：文章详情、任务日志这类「不打断当前上下文」的内容用它。 */
import { PhX } from '@phosphor-icons/vue'
import { DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'

import AppIconButton from './AppIconButton.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    width?: 'md' | 'lg'
  }>(),
  { description: '', width: 'md' },
)

const emit = defineEmits<{ (event: 'update:open', value: boolean): void }>()

const WIDTHS: Record<string, string> = {
  md: 'max-w-[520px]',
  lg: 'max-w-[720px]',
}
</script>

<template>
  <DialogRoot :open="props.open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-40 bg-overlay" />
      <DialogContent
        class="panel fixed top-0 right-0 z-50 flex h-[100dvh] w-full flex-col overflow-hidden rounded-none border-y-0 border-r-0 shadow-float sm:rounded-l-panel"
        :class="WIDTHS[width]"
      >
        <header class="flex items-start justify-between gap-4 border-b border-hairline px-5 py-4">
          <div class="min-w-0">
            <DialogTitle class="m-0 text-[15px] font-semibold break-words text-ink">
              {{ title }}
            </DialogTitle>
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
          class="flex flex-wrap items-center gap-2 border-t border-hairline bg-inset px-5 py-3"
        >
          <slot name="footer" />
        </footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
