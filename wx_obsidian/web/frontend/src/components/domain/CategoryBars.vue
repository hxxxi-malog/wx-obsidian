<script setup lang="ts">
/**
 * 分类分布：横向条 + 计数，Top N + 其余归并成「其他」。
 *
 * 为什么行内合并而不是让页面算：排序、归并、归一化是**同一件事的三个步骤**，
 * 拆到页面里只会让「宽度按谁归一」这种口径散落两处。
 *
 * 两条刻意的取舍：
 *  1. 归一化基准取**展示出来的行**（含「其他」）的最大值。旧实现按全量最大值算，
 *     一旦「其他」是多项之和就可能超过 100% 把条撑出容器。
 *  2. 「其他」不是一个真实分类，所以它不可点击；可点击的行是 <button>，
 *     键盘可达（旧的 div 行鼠标能点、键盘点不到）。
 *
 * 计数与跳转都由调用方决定：组件只 `emit('select', name)`，不碰路由。
 */
import { PhChartBar } from '@phosphor-icons/vue'
import { computed } from 'vue'

import AppEmptyState from '@/components/ui/AppEmptyState.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import { formatNumber } from '@/lib/format'

export interface CategoryCount {
  name: string
  count: number
}

interface BarRow {
  key: string
  name: string
  count: number
  /** 相对本组最大值（含「其他」）的百分比，0–100 */
  percent: number
  /** 归并出来的「其他」行：不是真实分类，不可点击 */
  bucket: boolean
  hint: string
}

const props = withDefaults(
  defineProps<{
    items: CategoryCount[]
    /** 展示条数上限，超出部分合并为「其他」（默认 Top 8 + 其他） */
    max?: number
  }>(),
  { max: 8 },
)

const emit = defineEmits<{ (event: 'select', name: string): void }>()

const hasItems = computed(() => props.items.length > 0)

const rows = computed<BarRow[]>(() => {
  const entries = props.items
    .map((item) => ({ name: item.name, count: Number(item.count) || 0 }))
    // 计数降序；Array.prototype.sort 稳定，同分保持后端返回顺序（与旧实现一致）
    .sort((left, right) => right.count - left.count)

  const limit = Math.max(1, props.max)
  const head = entries.slice(0, limit)
  const tail = entries.slice(limit)
  const visible = [...head]
  if (tail.length > 0) {
    visible.push({ name: '其他', count: tail.reduce((sum, item) => sum + item.count, 0) })
  }

  const peak = Math.max(1, ...visible.map((item) => item.count))
  return visible.map((item, index) => {
    const bucket = tail.length > 0 && index === visible.length - 1
    return {
      key: bucket ? '__bucket__' : item.name,
      name: item.name,
      count: item.count,
      percent: (item.count / peak) * 100,
      bucket,
      hint: bucket
        ? `其余 ${tail.length} 个分类合计 ${formatNumber(item.count)} 篇`
        : `${item.name}：${formatNumber(item.count)} 篇 · 点击筛选该分类的文章`,
    }
  })
})

function onRowClick(row: BarRow): void {
  if (row.bucket) {
    return
  }
  emit('select', row.name)
}
</script>

<template>
  <AppEmptyState
    v-if="!hasItems"
    compact
    :icon="PhChartBar"
    title="暂无分类数据"
    description="还没有成功处理的文章。先跑一轮抓取，生成的文章会按分类统计在这里。"
  >
    <template #action><slot name="empty-action" /></template>
  </AppEmptyState>

  <ul v-else class="m-0 flex list-none flex-col gap-0.5 p-0">
    <li v-for="row in rows" :key="row.key">
      <AppTooltip :content="row.hint">
        <component
          :is="row.bucket ? 'div' : 'button'"
          :type="row.bucket ? undefined : 'button'"
          class="row-hover flex w-full items-center gap-3 rounded-[6px] px-1.5 py-[7px] text-left"
          :class="row.bucket ? '' : 'cursor-pointer hover:bg-raised/70'"
          @click="onRowClick(row)"
        >
          <span class="w-[92px] shrink-0 truncate text-[13px] text-ink sm:w-[136px]">
            {{ row.name }}
          </span>

          <span class="h-[6px] min-w-0 flex-1 overflow-hidden rounded-full bg-inset">
            <span
              class="block h-full rounded-full bg-accent/75 transition-[width] duration-500 ease-out-expo"
              :class="row.count > 0 ? 'min-w-[3px]' : ''"
              :style="{ width: `${row.percent}%` }"
            />
          </span>

          <span class="w-10 shrink-0 text-right font-mono nums text-[12.5px] text-muted">
            {{ formatNumber(row.count) }}
          </span>
        </component>
      </AppTooltip>
    </li>
  </ul>
</template>
