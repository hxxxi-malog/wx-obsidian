<script setup lang="ts">
/**
 * 可搜索下拉（自建）。
 *
 * 为什么不用组件库：149 个分类的体验瓶颈在「输入即过滤 + 键盘选择 + 允许自由输入」，
 * 自己实现可以把这三件事都做对；同时避免 combobox 的过滤函数语义在不同版本间漂移。
 *
 * 无障碍：role=combobox + aria-expanded + aria-activedescendant，↑↓ 选择、↵ 确认、Esc 关闭。
 */
import { PhCaretUpDown, PhCheck, PhMagnifyingGlass } from '@phosphor-icons/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: { label: string; value: string; hint?: string }[]
    placeholder?: string
    disabled?: boolean
    /** 允许输入不在选项里的值（例如自定义模型渠道名） */
    allowCreate?: boolean
    /** 空字符串选项的显示文案（例如「全部分类」），给了就插到列表最前 */
    allLabel?: string
    emptyText?: string
    size?: 'sm' | 'md'
    id?: string
    invalid?: boolean
  }>(),
  {
    placeholder: '请选择',
    disabled: false,
    allowCreate: false,
    allLabel: '',
    emptyText: '没有匹配项',
    size: 'md',
    id: undefined,
    invalid: false,
  },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()

const open = ref(false)
const query = ref('')
const activeIndex = ref(0)
const rootRef = ref<HTMLElement | null>(null)

const items = computed(() => {
  const list = props.allLabel ? [{ label: props.allLabel, value: '' }, ...props.options] : props.options
  const term = query.value.trim().toLowerCase()
  if (!term) {
    return list
  }
  return list.filter(
    (item) => item.label.toLowerCase().includes(term) || item.value.toLowerCase().includes(term),
  )
})

const selectedLabel = computed(() => {
  if (props.modelValue === '' && props.allLabel) {
    return props.allLabel
  }
  return props.options.find((item) => item.value === props.modelValue)?.label ?? props.modelValue
})

const createCandidate = computed(() => {
  const term = query.value.trim()
  if (!props.allowCreate || !term) {
    return ''
  }
  const exists = items.value.some((item) => item.label === term || item.value === term)
  return exists ? '' : term
})

const canCreate = computed(() => createCandidate.value !== '')
const rowCount = computed(() => items.value.length + (canCreate.value ? 1 : 0))

function openMenu(): void {
  if (props.disabled) {
    return
  }
  open.value = true
  query.value = ''
  activeIndex.value = Math.max(
    0,
    items.value.findIndex((item) => item.value === props.modelValue),
  )
}

function closeMenu(): void {
  open.value = false
  query.value = ''
}

function choose(value: string): void {
  emit('update:modelValue', value)
  closeMenu()
}

function commitActive(): void {
  if (canCreate.value && activeIndex.value === items.value.length) {
    choose(createCandidate.value)
    return
  }
  const item = items.value[activeIndex.value]
  if (item) {
    choose(item.value)
  } else if (canCreate.value) {
    choose(createCandidate.value)
  }
}

function move(delta: number): void {
  if (!open.value) {
    openMenu()
    return
  }
  const total = rowCount.value
  if (total === 0) {
    return
  }
  activeIndex.value = (activeIndex.value + delta + total) % total
}

function onInputKeydown(event: KeyboardEvent): void {
  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault()
      move(1)
      break
    case 'ArrowUp':
      event.preventDefault()
      move(-1)
      break
    case 'Enter':
      event.preventDefault()
      commitActive()
      break
    case 'Escape':
      event.preventDefault()
      closeMenu()
      break
    case 'Tab':
      closeMenu()
      break
    default:
      if (!open.value) {
        open.value = true
      }
  }
}

function onDocumentPointerDown(event: PointerEvent): void {
  if (!open.value) {
    return
  }
  const target = event.target as Node | null
  if (target && rootRef.value && !rootRef.value.contains(target)) {
    closeMenu()
  }
}

watch(query, () => {
  activeIndex.value = 0
})

onMounted(() => document.addEventListener('pointerdown', onDocumentPointerDown, true))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocumentPointerDown, true))

const inputRef = ref<HTMLInputElement | null>(null)

function focusInput(): void {
  void nextTick(() => inputRef.value?.focus())
}
</script>

<template>
  <div ref="rootRef" class="relative">
    <div
      class="flex items-center gap-2 rounded-control border bg-inset px-3 transition-colors duration-150"
      :class="[
        size === 'sm' ? 'h-8' : 'h-9',
        invalid ? 'border-danger/60' : open ? 'border-strong' : 'border-hairline hover:border-strong',
        disabled ? 'opacity-50' : '',
      ]"
      @click="open ? focusInput() : (openMenu(), focusInput())"
    >
      <PhMagnifyingGlass v-if="open" :size="14" class="shrink-0 text-faint" aria-hidden="true" />
      <input
        ref="inputRef"
        :id="id"
        role="combobox"
        :aria-expanded="open"
        aria-autocomplete="list"
        :aria-activedescendant="open ? `combo-option-${activeIndex}` : undefined"
        :aria-invalid="invalid || undefined"
        :disabled="disabled"
        :value="open ? query : selectedLabel"
        :placeholder="open ? '输入以筛选…' : placeholder"
        class="min-w-0 flex-1 bg-transparent text-[13.5px] text-ink outline-none placeholder:text-faint"
        @input="query = ($event.target as HTMLInputElement).value"
        @keydown="onInputKeydown"
        @focus="openMenu"
      />
      <PhCaretUpDown :size="14" class="shrink-0 text-faint" aria-hidden="true" />
    </div>

    <div
      v-if="open"
      class="panel absolute z-50 mt-1.5 max-h-[300px] w-full overflow-y-auto rounded-panel p-1 shadow-float"
      role="listbox"
    >
      <button
        v-for="(item, index) in items"
        :id="`combo-option-${index}`"
        :key="`${item.value}-${index}`"
        type="button"
        role="option"
        :aria-selected="item.value === modelValue"
        class="flex w-full items-center gap-2 rounded-[8px] px-3 py-2 text-left text-[13px] text-ink transition-colors"
        :class="index === activeIndex ? 'bg-raised' : 'hover:bg-raised'"
        @mouseenter="activeIndex = index"
        @click="choose(item.value)"
      >
        <span class="truncate">{{ item.label }}</span>
        <span v-if="item.hint" class="ml-auto shrink-0 font-mono text-[11px] text-faint">
          {{ item.hint }}
        </span>
        <PhCheck
          v-if="item.value === modelValue"
          :size="14"
          class="ml-auto shrink-0 text-accent"
          aria-hidden="true"
        />
      </button>

      <button
        v-if="canCreate"
        :id="`combo-option-${items.length}`"
        type="button"
        role="option"
        :aria-selected="false"
        class="flex w-full items-center gap-2 rounded-[8px] px-3 py-2 text-left text-[13px] text-accent transition-colors"
        :class="activeIndex === items.length ? 'bg-raised' : 'hover:bg-raised'"
        @mouseenter="activeIndex = items.length"
        @click="choose(createCandidate)"
      >
        使用「{{ createCandidate }}」
      </button>

      <p v-if="rowCount === 0" class="m-0 px-3 py-2.5 text-[13px] text-faint">{{ emptyText }}</p>
    </div>
  </div>
</template>
