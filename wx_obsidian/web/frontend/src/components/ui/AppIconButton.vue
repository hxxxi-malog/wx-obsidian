<script setup lang="ts">
/** 图标按钮：label 必填（会变成 aria-label 与 title），禁止无标签的图标按钮。 */
import { computed } from 'vue'

import AppButton from './AppButton.vue'

const props = withDefaults(
  defineProps<{
    label: string
    variant?: 'solid' | 'tonal' | 'outline' | 'ghost' | 'danger'
    size?: 'sm' | 'md' | 'lg'
    loading?: boolean
    disabled?: boolean
  }>(),
  { variant: 'ghost', size: 'md', loading: false, disabled: false },
)

const padding = computed(() => {
  if (props.size === 'sm') {
    return 'w-8 px-0'
  }
  if (props.size === 'lg') {
    return 'w-11 px-0'
  }
  return 'w-9 px-0'
})
</script>

<template>
  <AppButton
    :variant="variant"
    :size="size"
    :loading="loading"
    :disabled="disabled"
    :class="padding"
    :aria-label="label"
    :title="label"
  >
    <slot />
  </AppButton>
</template>
