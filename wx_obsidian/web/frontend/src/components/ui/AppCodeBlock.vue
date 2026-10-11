<script setup lang="ts">
/** 等宽代码块：命令、日志片段。可换行、可限高、可复制。 */
import { computed } from 'vue'

import AppCopyButton from './AppCopyButton.vue'

const props = withDefaults(
  defineProps<{
    code: string
    copyLabel?: string
    wrap?: boolean
    maxHeight?: string
    muted?: boolean
  }>(),
  { copyLabel: '复制', wrap: true, maxHeight: '', muted: false },
)

const style = computed(() => ({ maxHeight: props.maxHeight || undefined }))
</script>

<template>
  <div class="group relative rounded-panel border border-hairline bg-inset">
    <div class="flex items-start gap-3 px-3 py-2.5">
      <pre
        class="m-0 min-w-0 flex-1 overflow-auto font-mono text-[12.5px] leading-relaxed"
        :class="[wrap ? 'whitespace-pre-wrap break-all' : 'whitespace-pre', muted ? 'text-muted' : 'text-ink']"
        :style="style"
      ><slot>{{ code }}</slot></pre>
      <!-- 常显：触屏设备没有 hover，藏起来的复制按钮等于没有 -->
      <div class="shrink-0 opacity-70 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <AppCopyButton :value="code" :label="copyLabel" />
      </div>
    </div>
  </div>
</template>
