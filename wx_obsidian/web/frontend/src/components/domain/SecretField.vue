<script setup lang="ts">
/**
 * 密钥 / 令牌输入：状态徽标 + 掩码回显 + 显示/隐藏。
 *
 * 两条必须讲清楚的语义（讲不清就会有人把已存的 Key 清掉）：
 *  1. **留空 = 不修改**，不是「清空」——由 store 的 buildConfigPayload 保证；
 *  2. 已保存的值只以掩码回显（后端 mask_secret），所以输入框永远是空的，
 *     状态只能靠徽标 + 掩码提示来表达，不能让人对着空框猜。
 */
import { computed } from 'vue'

import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppTag from '@/components/ui/AppTag.vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    label: string
    /** 后端回显的掩码；空串 = 未配置 */
    masked?: string
    placeholder?: string
    /** 覆盖默认提示（默认按是否已配置给出掩码或「尚未配置」） */
    hint?: string
    error?: string
    required?: boolean
    id?: string
  }>(),
  {
    masked: '',
    placeholder: '留空则不修改',
    hint: '',
    error: '',
    required: false,
    id: '',
  },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'enter'): void
}>()

const configured = computed(() => props.masked !== '')

/** 掩码必须一直看得见：它是「服务端到底存了哪一把钥匙」的唯一线索 */
const resolvedHint = computed(() => {
  if (!configured.value) {
    return props.hint || '尚未配置'
  }
  const masked = `当前已保存：${props.masked}`
  return props.hint ? `${props.hint}；${masked}` : masked
})
</script>

<template>
  <AppField :label="label" :hint="resolvedHint" :error="error" :required="required" :html-for="id">
    <template #labelAction>
      <AppTag :tone="configured ? 'success' : 'neutral'" variant="outline">
        {{ configured ? '已配置' : '未配置' }}
      </AppTag>
    </template>

    <template #default="{ id: fieldId, invalid }">
      <AppInput
        :id="fieldId"
        :model-value="modelValue"
        type="password"
        revealable
        mono
        :invalid="invalid"
        :placeholder="placeholder"
        @update:model-value="emit('update:modelValue', $event)"
        @enter="emit('enter')"
      />
    </template>
  </AppField>
</template>
