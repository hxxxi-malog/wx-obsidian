<script setup lang="ts">
/** 悬浮提示：只放一句话，不放交互内容（要交互用 Popover）。 */
import { TooltipContent, TooltipPortal, TooltipProvider, TooltipRoot, TooltipTrigger } from 'reka-ui'

withDefaults(
  defineProps<{
    content: string
    side?: 'top' | 'right' | 'bottom' | 'left'
    disabled?: boolean
  }>(),
  { side: 'top', disabled: false },
)
</script>

<template>
  <TooltipProvider :delay-duration="260" :skip-delay-duration="300">
    <TooltipRoot :disabled="disabled">
      <TooltipTrigger as-child>
        <slot />
      </TooltipTrigger>
      <TooltipPortal>
        <TooltipContent
          :side="side"
          :side-offset="8"
          class="panel z-50 max-w-[280px] rounded-[10px] px-2.5 py-1.5 text-xs leading-relaxed text-ink shadow-float"
        >
          {{ content }}
        </TooltipContent>
      </TooltipPortal>
    </TooltipRoot>
  </TooltipProvider>
</template>
