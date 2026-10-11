<script setup lang="ts" generic="T extends Record<string, unknown>">
/**
 * 数据表格：粘性表头 + 发丝行线 + 可选多选/排序/键盘导航 + 骨架加载态。
 *
 * 设计取舍：
 *  - 不用卡片式表格（每行一个盒子），用「发丝线 + 悬浮高亮」保持高密度下的可扫读性；
 *  - 排序是**受控**的（emit sort），服务端排序与当前页排序都能接；
 *  - 单元格内容通过 `#cell-<key>` 插槽自定义，表格本身不猜业务样式。
 */
import { PhCaretDown, PhCaretUp, PhCaretUpDown } from '@phosphor-icons/vue'
import { computed, ref, watch } from 'vue'

import AppSkeleton from './AppSkeleton.vue'

export interface DataTableColumn {
  key: string
  label: string
  /** CSS 宽度，例如 '120px'；不填则自适应 */
  width?: string
  align?: 'left' | 'right' | 'center'
  sortable?: boolean
  mono?: boolean
  /** 小屏隐藏：'sm' | 'md' */
  hideBelow?: 'sm' | 'md'
  headerClass?: string
  cellClass?: string
}

const props = withDefaults(
  defineProps<{
    columns: DataTableColumn[]
    rows: T[]
    rowKey: (row: T) => string
    loading?: boolean
    /** 骨架行数（贴近真实数据量，别只给 3 行） */
    skeletonRows?: number
    selectable?: boolean
    selected?: string[]
    density?: 'comfortable' | 'compact'
    sortKey?: string
    sortOrder?: 'asc' | 'desc'
    /** 行可点击（打开详情等） */
    clickable?: boolean
    /** 行被选中时的强调（例如从仪表盘跳转过来的那条） */
    highlightKey?: string
    /** 滚动区高度上限（给了它，粘性表头才有意义：超出部分在表格内部滚动） */
    maxHeight?: string
  }>(),
  {
    loading: false,
    skeletonRows: 6,
    selectable: false,
    selected: () => [],
    density: 'comfortable',
    sortKey: '',
    sortOrder: 'asc',
    clickable: false,
    highlightKey: '',
    maxHeight: '',
  },
)

const emit = defineEmits<{
  (event: 'update:selected', value: string[]): void
  (event: 'sort', key: string): void
  (event: 'rowClick', row: T): void
  (event: 'rowEnter', row: T): void
}>()

const rowRefs = ref<HTMLElement[]>([])
const activeIndex = ref(-1)

/**
 * 选择集的本地镜像：同一个 tick 内连续点两个复选框时，props 还没回流，
 * 直接读 props.selected 会丢掉前一次改动。镜像在 props 变化时同步。
 */
const localSelected = ref<string[]>([...props.selected])
watch(
  () => props.selected,
  (value) => {
    localSelected.value = [...value]
  },
)

const scrollStyle = computed(() =>
  props.maxHeight ? { maxHeight: props.maxHeight, overflowY: 'auto' as const } : undefined,
)

const rowHeight = computed(() => (props.density === 'compact' ? 'py-2' : 'py-3'))
const selectedSet = computed(() => new Set(localSelected.value))
const allSelected = computed(
  () => props.rows.length > 0 && props.rows.every((row) => selectedSet.value.has(props.rowKey(row))),
)
const someSelected = computed(() => props.selected.length > 0 && !allSelected.value)

function valueOf(row: T, key: string): unknown {
  return row[key]
}

function toggleAll(): void {
  const next = allSelected.value ? [] : props.rows.map((row) => props.rowKey(row))
  localSelected.value = next
  emit('update:selected', next)
}

function toggleRow(row: T): void {
  const key = props.rowKey(row)
  const next = new Set(localSelected.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  localSelected.value = Array.from(next)
  emit('update:selected', localSelected.value)
}

function onSort(column: DataTableColumn): void {
  if (column.sortable) {
    emit('sort', column.key)
  }
}

function onRowKeydown(event: KeyboardEvent, row: T, index: number): void {
  if (event.key === 'Enter') {
    event.preventDefault()
    emit('rowEnter', row)
    return
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const delta = event.key === 'ArrowDown' ? 1 : -1
    const next = Math.min(props.rows.length - 1, Math.max(0, index + delta))
    activeIndex.value = next
    rowRefs.value[next]?.focus()
  }
}

function setRowRef(element: unknown, index: number): void {
  if (element instanceof HTMLElement) {
    rowRefs.value[index] = element
  }
}

const alignClass = (column: DataTableColumn): string =>
  column.align === 'right' ? 'text-right' : column.align === 'center' ? 'text-center' : 'text-left'

const hideClass = (column: DataTableColumn): string =>
  column.hideBelow === 'md' ? 'hidden md:table-cell' : column.hideBelow === 'sm' ? 'hidden sm:table-cell' : ''
</script>

<template>
  <div class="w-full overflow-x-auto" :style="scrollStyle">
    <table class="w-full border-separate border-spacing-0 text-[13px]">
      <thead>
        <tr>
          <th
            v-if="selectable"
            class="sticky top-0 z-10 w-10 border-b border-hairline bg-panel px-3 py-2.5"
          >
            <input
              type="checkbox"
              class="size-3.5 cursor-pointer accent-[var(--accent)]"
              :checked="allSelected"
              :indeterminate="someSelected"
              aria-label="全选本页"
              @change="toggleAll"
            />
          </th>
          <th
            v-for="column in columns"
            :key="column.key"
            class="sticky top-0 z-10 border-b border-hairline bg-panel px-3 py-2.5 text-[11.5px] font-medium tracking-wide text-faint whitespace-nowrap"
            :class="[alignClass(column), hideClass(column), column.sortable ? 'cursor-pointer select-none hover:text-ink' : '', column.headerClass]"
            :style="column.width ? { width: column.width } : undefined"
            :aria-sort="
              column.sortable && sortKey === column.key
                ? sortOrder === 'asc'
                  ? 'ascending'
                  : 'descending'
                : undefined
            "
            @click="onSort(column)"
          >
            <span class="inline-flex items-center gap-1">
              {{ column.label }}
              <template v-if="column.sortable">
                <PhCaretUp
                  v-if="sortKey === column.key && sortOrder === 'asc'"
                  :size="11"
                  class="text-accent"
                  aria-hidden="true"
                />
                <PhCaretDown
                  v-else-if="sortKey === column.key"
                  :size="11"
                  class="text-accent"
                  aria-hidden="true"
                />
                <PhCaretUpDown v-else :size="11" class="opacity-50" aria-hidden="true" />
              </template>
            </span>
          </th>
        </tr>
      </thead>

      <tbody v-if="loading">
        <tr v-for="index in skeletonRows" :key="`skeleton-${index}`">
          <td v-if="selectable" class="border-b border-hairline px-3" :class="rowHeight">
            <AppSkeleton variant="line" width="14px" />
          </td>
          <td
            v-for="column in columns"
            :key="`${column.key}-${index}`"
            class="border-b border-hairline px-3"
            :class="[hideClass(column), rowHeight]"
          >
            <AppSkeleton variant="line" :width="index % 3 === 0 ? '72%' : index % 3 === 1 ? '56%' : '84%'" />
          </td>
        </tr>
      </tbody>

      <tbody v-else-if="rows.length === 0">
        <tr>
          <td :colspan="columns.length + (selectable ? 1 : 0)" class="px-3">
            <slot name="empty" />
          </td>
        </tr>
      </tbody>

      <tbody v-else>
        <tr
          v-for="(row, index) in rows"
          :key="rowKey(row)"
          :ref="(element) => setRowRef(element, index)"
          :tabindex="clickable ? 0 : undefined"
          class="row-hover hover:bg-raised/60 outline-none"
          :class="[
            highlightKey && rowKey(row) === highlightKey ? 'bg-accent-soft' : '',
            clickable ? 'cursor-pointer' : '',
            activeIndex === index ? 'bg-raised' : '',
          ]"
          @click="clickable ? emit('rowClick', row) : undefined"
          @keydown="onRowKeydown($event, row, index)"
          @focus="activeIndex = index"
        >
          <td v-if="selectable" class="border-b border-hairline px-3" :class="rowHeight" @click.stop>
            <input
              type="checkbox"
              class="size-3.5 cursor-pointer accent-[var(--accent)]"
              :checked="selectedSet.has(rowKey(row))"
              :aria-label="`选择 ${rowKey(row)}`"
              @change="toggleRow(row)"
            />
          </td>
          <td
            v-for="column in columns"
            :key="column.key"
            class="border-b border-hairline px-3 align-middle text-ink"
            :class="[alignClass(column), hideClass(column), column.mono ? 'font-mono nums' : '', rowHeight, column.cellClass]"
          >
            <slot :name="`cell-${column.key}`" :row="row" :value="valueOf(row, column.key)" :index="index">
              {{ valueOf(row, column.key) ?? '—' }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
