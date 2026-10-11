<script setup lang="ts">
/**
 * 文本输入：单行、密码（可显隐）、URL 等。
 * 用原生 input 而不是包装组件库——转换层越薄，键盘/输入法行为越可靠。
 */
import { PhEye, PhEyeSlash, PhX } from '@phosphor-icons/vue'
import { computed, ref, useAttrs } from 'vue'

// 属性要落到真正的 input 上（data-* 标记、aria-*、name、autofocus…），
// 而不是包裹用的 div；class/style 例外，仍然留在外层容器上。
defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    modelValue: string
    type?: 'text' | 'password' | 'url' | 'email' | 'search'
    placeholder?: string
    invalid?: boolean
    disabled?: boolean
    readonly?: boolean
    mono?: boolean
    clearable?: boolean
    autocomplete?: string
    spellcheck?: boolean
    /** 密码框显示「显示/隐藏」按钮 */
    revealable?: boolean
    size?: 'sm' | 'md'
    id?: string
  }>(),
  {
    type: 'text',
    placeholder: '',
    invalid: false,
    disabled: false,
    readonly: false,
    mono: false,
    clearable: false,
    autocomplete: 'off',
    spellcheck: false,
    revealable: false,
    size: 'md',
    id: undefined,
  },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'enter'): void
}>()

const attrs = useAttrs()
/** class/style 留在外层容器上（宽度、栅格跨度这类布局由调用方决定） */
const wrapperAttrs = computed(() => ({ class: attrs.class, style: attrs.style }))
/** 其余属性（data-*、aria-*、name、id、autofocus…）落到真正的 input 上 */
const inputAttrs = computed(() => {
  const rest: Record<string, unknown> = { ...attrs }
  delete rest.class
  delete rest.style
  return rest
})

const revealed = ref(false)
const inputType = computed(() => {
  if (props.type === 'password' && props.revealable && revealed.value) {
    return 'text'
  }
  return props.type
})

const hasValue = computed(() => props.modelValue !== '')

function onInput(event: Event): void {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}

const wrapper = computed(() => [
  'group flex items-center gap-2 rounded-control border bg-inset transition-colors duration-150',
  props.size === 'sm' ? 'h-8 px-2.5' : 'h-9 px-3',
  props.invalid ? 'border-danger/60' : 'border-hairline focus-within:border-strong',
  props.disabled ? 'opacity-50' : '',
])

const inputClasses = computed(() => [
  'min-w-0 flex-1 bg-transparent text-[13.5px] text-ink outline-none placeholder:text-faint',
  props.mono ? 'font-mono nums' : '',
])
</script>

<template>
  <div :class="[wrapper, wrapperAttrs.class]" :style="wrapperAttrs.style">
    <slot name="prefix" />
    <input
      :id="id"
      :type="inputType"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      :readonly="readonly"
      :autocomplete="autocomplete"
      :spellcheck="spellcheck"
      :aria-invalid="invalid || undefined"
      :class="inputClasses"
      v-bind="inputAttrs"
      @input="onInput"
      @keyup.enter="emit('enter')"
    />
    <button
      v-if="clearable && hasValue && !disabled"
      type="button"
      class="shrink-0 rounded-[6px] p-0.5 text-faint transition-colors hover:text-ink"
      aria-label="清空"
      @click="emit('update:modelValue', '')"
    >
      <PhX :size="14" aria-hidden="true" />
    </button>
    <button
      v-if="type === 'password' && revealable"
      type="button"
      class="shrink-0 rounded-[6px] p-0.5 text-faint transition-colors hover:text-ink"
      :aria-label="revealed ? '隐藏' : '显示'"
      @click="revealed = !revealed"
    >
      <PhEyeSlash v-if="revealed" :size="15" aria-hidden="true" />
      <PhEye v-else :size="15" aria-hidden="true" />
    </button>
    <slot name="suffix" />
  </div>
</template>
