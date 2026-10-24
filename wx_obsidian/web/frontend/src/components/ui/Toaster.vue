<script setup lang="ts">
/**
 * 全局提示层（替代 ElMessage）。
 * 位置固定在右上角（不与右下角的任务坞抢位置），role=status + aria-live=polite。
 */
import {
  PhCheckCircle,
  PhInfo,
  PhWarning,
  PhWarningCircle,
  PhX,
} from '@phosphor-icons/vue'
import type { Component } from 'vue'

import { useUiStore, type Toast } from '@/stores/ui'

const ui = useUiStore()

const ICONS: Record<Toast['tone'], Component> = {
  success: PhCheckCircle,
  warning: PhWarning,
  danger: PhWarningCircle,
  info: PhInfo,
}

const TONE_CLASS: Record<Toast['tone'], string> = {
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  info: 'text-info',
}
</script>

<template>
  <div
    class="pointer-events-none fixed top-[68px] right-4 z-[60] flex w-[min(380px,calc(100vw-32px))] flex-col gap-2"
    role="status"
    aria-live="polite"
    data-toaster
  >
    <TransitionGroup
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="opacity-0 translate-y-[-6px]"
      leave-active-class="transition duration-150 ease-in"
      leave-to-class="opacity-0 translate-x-2"
      move-class="transition duration-150"
    >
      <div
        v-for="toast in ui.toasts"
        :key="toast.id"
        class="panel pointer-events-auto flex items-start gap-2.5 rounded-panel px-3.5 py-3 shadow-float"
      >
        <component
          :is="ICONS[toast.tone]"
          :size="17"
          class="mt-px shrink-0"
          :class="TONE_CLASS[toast.tone]"
          aria-hidden="true"
        />
        <div class="min-w-0 flex-1">
          <p class="m-0 text-[13.5px] font-medium text-ink">{{ toast.title }}</p>
          <p
            v-if="toast.description"
            class="m-0 mt-0.5 text-xs leading-relaxed whitespace-pre-line text-muted"
          >
            {{ toast.description }}
          </p>
          <button
            v-if="toast.action"
            type="button"
            class="mt-2 text-xs font-medium text-accent transition-opacity hover:opacity-80"
            @click="toast.action.run(); ui.dismissToast(toast.id)"
          >
            {{ toast.action.label }}
          </button>
        </div>
        <button
          type="button"
          class="shrink-0 rounded-[6px] p-0.5 text-faint transition-colors hover:text-ink"
          aria-label="关闭提示"
          @click="ui.dismissToast(toast.id)"
        >
          <PhX :size="13" aria-hidden="true" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>
