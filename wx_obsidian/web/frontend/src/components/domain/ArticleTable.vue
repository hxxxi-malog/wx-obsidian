<script setup lang="ts">
/**
 * 文章表格（domain 层）：把 AppDataTable 包成「文章」这一领域的投影。
 *
 * 为什么单独一层：列定义与单元格渲染是领域知识（状态色调、文件在库与否、时间格式），
 * 不该混进视图；视图只负责取数与编排对话框，表格可被其它页面复用。
 *
 * 高密度取舍：
 *  - 分类/子主题**单行截断**（line-clamp-1），绝不换行撑高行高，全文一律走 Tooltip；
 *  - 标题两行截断（旧版是一行省略号），且是唯一的自适应列，md 起给足 min-width；
 *  - 次要列 hideBelow=md：<768px 只剩标题/状态/操作，单列且不横向滚动；
 *  - 行单击 = 开详情抽屉，行内 ↵ = 在 Obsidian 打开（旧版双击改为键盘/按钮，让位给抽屉）。
 */
import { PhArrowSquareOut, PhTrash } from '@phosphor-icons/vue'
import { computed } from 'vue'

import AppDataTable, { type DataTableColumn } from '@/components/ui/AppDataTable.vue'
import AppIconButton from '@/components/ui/AppIconButton.vue'
import AppTag from '@/components/ui/AppTag.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import { formatDateTime, formatRelative } from '@/lib/format'
import { articleStatusMeta } from '@/lib/status'

/**
 * AppDataTable 的泛型约束是 `Record<string, unknown>`，而接口层 DTO `ArticleItem` 是
 * interface —— interface 不带隐式索引签名，直接传会编译不过。这里用结构化等价的 type
 * 别名做行类型：`ArticleItem[]` 可以原样传进来，插槽里的 row 依旧是强类型。
 */
type ArticleRow = {
  article_id: string
  title: string
  category: string
  sub_topic: string
  status: string
  file_path: string
  file_exists: boolean
  processed_at: string
  error: string
  retry_count: number
}

const props = withDefaults(
  defineProps<{
    rows: ArticleRow[]
    loading?: boolean
    /** 当前页选中的 article_id（与 AppDataTable 的 selected 契约一致） */
    selected?: string[]
    sortKey?: string
    sortOrder?: 'asc' | 'desc'
    /** 骨架行数：贴近每页条数，加载时页面高度不跳 */
    skeletonRows?: number
    selectable?: boolean
    /** 批量操作进行中：行内按钮一并禁用，避免与批量任务抢 processed.json */
    busy?: boolean
  }>(),
  {
    loading: false,
    selected: () => [],
    sortKey: '',
    sortOrder: 'desc',
    skeletonRows: 20,
    selectable: true,
    busy: false,
  },
)

const emit = defineEmits<{
  (event: 'update:selected', value: string[]): void
  (event: 'sort', key: string): void
  (event: 'rowClick', row: ArticleRow): void
  (event: 'rowEnter', row: ArticleRow): void
  (event: 'open', row: ArticleRow): void
  (event: 'remove', row: ArticleRow): void
}>()

/** 骨架最多 30 行：每页 100 时铺 100 行骨架只会让加载态自己变成一次长列表抖动 */
const skeletonCount = computed(() => Math.min(Math.max(1, props.skeletonRows), 30))

/** 列宽显式给定（标题列除外，它是唯一的自适应列）：列宽一稳，行高压根没机会抖 */
const columns: DataTableColumn[] = [
  {
    key: 'title',
    label: '标题',
    sortable: true,
    headerClass: 'min-w-[120px] md:min-w-[260px]',
    cellClass: 'min-w-[120px] md:min-w-[260px]',
  },
  { key: 'category', label: '分类', width: '160px', hideBelow: 'md', cellClass: 'max-w-[160px]' },
  { key: 'sub_topic', label: '子主题', width: '150px', hideBelow: 'md', cellClass: 'max-w-[150px]' },
  { key: 'status', label: '状态', width: '92px' },
  { key: 'file', label: '文件', width: '76px', align: 'center', hideBelow: 'md' },
  { key: 'processed_at', label: '处理时间', width: '176px', sortable: true, mono: true, hideBelow: 'md' },
  { key: 'actions', label: '操作', width: '104px', align: 'right' },
]

const statusOf = (row: ArticleRow) => articleStatusMeta(row.status)
/** 空分类/空子主题用弱化文案，避免一整列都是空标签 */
const orDash = (value: string): string => value || '—'
</script>

<template>
  <div>
    <AppDataTable
      :columns="columns"
      :rows="rows"
      :row-key="(row) => row.article_id"
      :loading="loading"
      :skeleton-rows="skeletonCount"
      max-height="60dvh"
      :selectable="selectable"
      :selected="selected"
      :sort-key="sortKey"
      :sort-order="sortOrder"
      :clickable="true"
      density="compact"
      @update:selected="emit('update:selected', $event)"
      @sort="emit('sort', $event)"
      @row-click="emit('rowClick', $event)"
      @row-enter="emit('rowEnter', $event)"
    >
      <!-- 标题：两行截断 + 全文 Tooltip（侧向弹出，避免遮住下一行） -->
      <template #cell-title="{ row }">
        <AppTooltip :content="row.title" side="right" :disabled="!row.title">
          <div class="line-clamp-2 leading-snug font-medium [overflow-wrap:anywhere]">
            {{ orDash(row.title) }}
          </div>
        </AppTooltip>
      </template>

      <!-- 分类：AppTag + 单行截断。标签自身封顶宽度，长分类名不会把整列顶宽 -->
      <template #cell-category="{ row }">
        <AppTooltip :content="row.category" side="top" :disabled="!row.category">
          <AppTag v-if="row.category" variant="outline" class="max-w-[136px]">
            <span class="min-w-0 truncate">{{ row.category }}</span>
          </AppTag>
          <span v-else class="text-faint">—</span>
        </AppTooltip>
      </template>

      <template #cell-sub_topic="{ row }">
        <AppTooltip :content="row.sub_topic" side="top" :disabled="!row.sub_topic">
          <div class="line-clamp-1 text-muted" :class="row.sub_topic ? '' : 'text-faint'">
            {{ orDash(row.sub_topic) }}
          </div>
        </AppTooltip>
      </template>

      <template #cell-status="{ row }">
        <AppTag :tone="statusOf(row).tone" variant="soft">{{ statusOf(row).label }}</AppTag>
      </template>

      <!-- 文件：在库 / 缺失。用 outline 小标签，不与状态标签抢视觉 -->
      <template #cell-file="{ row }">
        <AppTag :tone="row.file_exists ? 'success' : 'danger'" variant="outline">
          {{ row.file_exists ? '在库' : '缺失' }}
        </AppTag>
      </template>

      <template #cell-processed_at="{ row }">
        <AppTooltip :content="formatRelative(row.processed_at)" side="top" :disabled="!row.processed_at">
          <span class="whitespace-nowrap">{{ formatDateTime(row.processed_at) }}</span>
        </AppTooltip>
      </template>

      <template #cell-actions="{ row }">
        <div class="flex items-center justify-end gap-0.5">
          <AppIconButton
            label="在 Obsidian 中打开"
            size="sm"
            variant="ghost"
            :disabled="busy"
            @click.stop="emit('open', row)"
          >
            <PhArrowSquareOut :size="14" aria-hidden="true" />
          </AppIconButton>
          <AppIconButton
            label="删除"
            size="sm"
            variant="ghost"
            class="group"
            :disabled="busy"
            @click.stop="emit('remove', row)"
          >
            <PhTrash :size="14" class="transition-colors group-hover:text-danger" aria-hidden="true" />
          </AppIconButton>
        </div>
      </template>

      <template #empty>
        <slot name="empty" />
      </template>
    </AppDataTable>
  </div>
</template>


