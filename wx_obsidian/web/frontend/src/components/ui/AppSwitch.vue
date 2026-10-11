<script setup lang="ts">
/** 开关：Reka 的 SwitchRoot（自带键盘与 aria 语义），皮肤自己写。 */
import { SwitchRoot, SwitchThumb } from 'reka-ui'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    disabled?: boolean
    label?: string
    size?: 'sm' | 'md'
  }>(),
  { disabled: false, label: '', size: 'md' },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: boolean): void }>()

const dims = props.size === 'sm' ? { root: 'h-5 w-9', thumb: 16 } : { root: 'h-6 w-11', thumb: 20 }
</script>

<template>
  <SwitchRoot
    :model-value="modelValue"
    :disabled="disabled"
    :aria-label="label || undefined"
    class="relative inline-flex shrink-0 cursor-pointer items-center rounded-full border border-hairline bg-raised transition-colors duration-200 ease-out-expo data-[state=checked]:border-accent/50 data-[state=checked]:bg-accent-soft data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50"
    :class="dims.root"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <SwitchThumb
      class="block translate-x-0.5 rounded-full bg-faint transition-transform duration-200 ease-out-expo data-[state=checked]:translate-x-[calc(100%+2px)] data-[state=checked]:bg-accent"
      :style="{ width: `${dims.thumb}px`, height: `${dims.thumb}px` }"
    />
  </SwitchRoot>
</template>
