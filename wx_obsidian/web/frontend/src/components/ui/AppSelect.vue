<script setup lang="ts">
/**
 * 下拉选择（不可搜索）。
 * 需要搜索/自由输入的场景用 AppCombobox——那个是自建的，行为更可控。
 */
import { PhCaretUpDown, PhCheck } from '@phosphor-icons/vue'
import {
  SelectContent,
  SelectIcon,
  SelectItem,
  SelectItemIndicator,
  SelectItemText,
  SelectPortal,
  SelectRoot,
  SelectTrigger,
  SelectValue,
  SelectViewport,
} from 'reka-ui'

export interface SelectOption {
  label: string
  value: string
  hint?: string
  disabled?: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: SelectOption[]
    placeholder?: string
    disabled?: boolean
    invalid?: boolean
    size?: 'sm' | 'md'
    id?: string
    mono?: boolean
  }>(),
  {
    placeholder: '请选择',
    disabled: false,
    invalid: false,
    size: 'md',
    id: undefined,
    mono: false,
  },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()
</script>

<template>
  <SelectRoot
    :model-value="modelValue"
    :disabled="disabled"
    @update:model-value="emit('update:modelValue', String($event))"
  >
    <SelectTrigger
      :id="id"
      :aria-invalid="invalid || undefined"
      class="flex w-full items-center justify-between gap-2 rounded-control border bg-inset px-3 text-left text-[13.5px] text-ink transition-colors duration-150 hover:border-strong focus:border-strong data-[placeholder]:text-faint data-[disabled]:opacity-50"
      :class="[size === 'sm' ? 'h-8' : 'h-9', invalid ? 'border-danger/60' : 'border-hairline', mono ? 'font-mono nums' : '']"
    >
      <SelectValue :placeholder="placeholder" class="truncate" />
      <SelectIcon class="shrink-0 text-faint">
        <PhCaretUpDown :size="14" aria-hidden="true" />
      </SelectIcon>
    </SelectTrigger>

    <SelectPortal>
      <SelectContent
        position="popper"
        :side-offset="6"
        class="panel z-50 max-h-[320px] min-w-[var(--reka-select-trigger-width)] overflow-hidden rounded-panel p-1 shadow-float"
      >
        <SelectViewport class="max-h-[300px] overflow-y-auto">
          <SelectItem
            v-for="option in props.options"
            :key="option.value"
            :value="option.value"
            :disabled="option.disabled"
            class="relative flex cursor-pointer items-center gap-2 rounded-[8px] py-2 pr-8 pl-3 text-[13px] text-ink outline-none select-none data-[disabled]:cursor-not-allowed data-[disabled]:opacity-40 data-[highlighted]:bg-raised"
          >
            <SelectItemText class="truncate">{{ option.label }}</SelectItemText>
            <span v-if="option.hint" class="ml-auto shrink-0 font-mono text-[11px] text-faint">
              {{ option.hint }}
            </span>
            <SelectItemIndicator class="absolute right-2.5 text-accent">
              <PhCheck :size="14" aria-hidden="true" />
            </SelectItemIndicator>
          </SelectItem>
        </SelectViewport>
      </SelectContent>
    </SelectPortal>
  </SelectRoot>
</template>
