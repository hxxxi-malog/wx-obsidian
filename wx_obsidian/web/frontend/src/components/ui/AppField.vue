<script setup lang="ts">
/**
 * 表单字段容器：label 在输入框**上方**（skill 硬性要求），错误在下方，帮助文本可选。
 */
import { computed, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    error?: string
    required?: boolean
    /** 右侧与 label 同一行的补充信息（例如「已配置」徽标） */
    htmlFor?: string
  }>(),
  { label: '', hint: '', error: '', required: false, htmlFor: '' },
)

const fieldId = useId()
const controlId = computed(() => props.htmlFor || fieldId)
</script>

<template>
  <div class="flex flex-col gap-1.5">
    <div v-if="label || $slots.labelAction" class="flex items-center justify-between gap-3">
      <label v-if="label" :for="controlId" class="text-[12.5px] font-medium text-muted">
        {{ label }}
        <span v-if="required" class="text-danger" aria-hidden="true">*</span>
      </label>
      <slot name="labelAction" />
    </div>

    <slot :id="controlId" :invalid="Boolean(error)" />

    <p v-if="error" class="m-0 text-xs leading-relaxed text-danger">{{ error }}</p>
    <p v-else-if="hint" class="m-0 text-xs leading-relaxed text-faint">{{ hint }}</p>
  </div>
</template>
