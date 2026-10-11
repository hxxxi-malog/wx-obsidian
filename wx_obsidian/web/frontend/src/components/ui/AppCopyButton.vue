<script setup lang="ts">
/**
 * 复制按钮：命令、路径、ID 都用它。
 * 剪贴板被浏览器拒绝时（非 https / 无权限）如实提示，不假装成功。
 */
import { PhCheck, PhCopy } from '@phosphor-icons/vue'
import { ref } from 'vue'

import { useToast } from '@/composables/useToast'
import AppButton from './AppButton.vue'

const props = withDefaults(
  defineProps<{
    value: string
    label?: string
    size?: 'sm' | 'md'
    variant?: 'ghost' | 'outline' | 'tonal'
    successMessage?: string
  }>(),
  { label: '复制', size: 'sm', variant: 'ghost', successMessage: '已复制' },
)

const toast = useToast()
const copied = ref(false)

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.value)
    copied.value = true
    toast.success(props.successMessage)
    window.setTimeout(() => (copied.value = false), 1600)
  } catch {
    toast.warn('浏览器拒绝了剪贴板访问，请手动选中复制')
  }
}
</script>

<template>
  <AppButton :size="size" :variant="variant" @click="copy">
    <template #icon>
      <PhCheck v-if="copied" :size="14" class="text-success" aria-hidden="true" />
      <PhCopy v-else :size="14" aria-hidden="true" />
    </template>
    <slot>{{ copied ? '已复制' : label }}</slot>
  </AppButton>
</template>
