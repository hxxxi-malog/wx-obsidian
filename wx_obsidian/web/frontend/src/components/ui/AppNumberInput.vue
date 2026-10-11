<script setup lang="ts">
/** 数字输入：带步进按钮与快捷预设（抓取条数这类「常用值就那几档」的场景）。 */
import { PhMinus, PhPlus } from '@phosphor-icons/vue'
import { computed, useAttrs } from 'vue'

// 与 AppInput 一致：非 class/style 的属性落到真正的 input 上（id/aria-*/data-*）
defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    modelValue: number
    min?: number
    max?: number
    step?: number
    presets?: number[]
    suffix?: string
    disabled?: boolean
    invalid?: boolean
    size?: 'sm' | 'md'
    id?: string
  }>(),
  {
    min: 0,
    max: Number.MAX_SAFE_INTEGER,
    step: 1,
    presets: () => [],
    suffix: '',
    disabled: false,
    invalid: false,
    size: 'md',
    id: undefined,
  },
)

const attrs = useAttrs()
const inputAttrs = computed(() => {
  const rest: Record<string, unknown> = { ...attrs }
  delete rest.class
  delete rest.style
  return rest
})

const emit = defineEmits<{ (event: 'update:modelValue', value: number): void }>()

const clamped = computed(() => Math.min(props.max, Math.max(props.min, Number(props.modelValue) || 0)))

function set(value: number): void {
  if (props.disabled) {
    return
  }
  const next = Math.min(props.max, Math.max(props.min, Math.round(value)))
  emit('update:modelValue', next)
}

function onInput(event: Event): void {
  const raw = Number((event.target as HTMLInputElement).value)
  if (Number.isNaN(raw)) {
    return
  }
  set(raw)
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-2" :class="attrs.class" :style="attrs.style">
    <div
      class="inline-flex items-center rounded-control border bg-inset transition-colors"
      :class="[size === 'sm' ? 'h-8' : 'h-9', invalid ? 'border-danger/60' : 'border-hairline focus-within:border-strong', disabled ? 'opacity-50' : '']"
    >
      <button
        type="button"
        class="grid h-full w-8 place-items-center text-faint transition-colors hover:text-ink disabled:opacity-40"
        :disabled="disabled || clamped <= min"
        aria-label="减少"
        @click="set(clamped - step)"
      >
        <PhMinus :size="13" aria-hidden="true" />
      </button>
      <input
        :id="id"
        :value="clamped"
        type="text"
        inputmode="numeric"
        class="nums h-full w-14 border-x border-hairline bg-transparent text-center font-mono text-[13.5px] text-ink outline-none"
        :disabled="disabled"
        v-bind="inputAttrs"
        @change="onInput"
      />
      <button
        type="button"
        class="grid h-full w-8 place-items-center text-faint transition-colors hover:text-ink disabled:opacity-40"
        :disabled="disabled || clamped >= max"
        aria-label="增加"
        @click="set(clamped + step)"
      >
        <PhPlus :size="13" aria-hidden="true" />
      </button>
    </div>

    <span v-if="suffix" class="text-xs text-faint">{{ suffix }}</span>

    <div v-if="presets.length" class="flex items-center gap-1.5">
      <button
        v-for="preset in presets"
        :key="preset"
        type="button"
        class="nums rounded-[7px] border px-2 py-1 font-mono text-[11.5px] transition-colors"
        :class="
          clamped === preset
            ? 'border-accent/45 bg-accent-soft text-accent'
            : 'border-hairline text-faint hover:border-strong hover:text-ink'
        "
        :disabled="disabled"
        @click="set(preset)"
      >
        {{ preset === 0 ? '不限' : preset }}
      </button>
    </div>
  </div>
</template>
