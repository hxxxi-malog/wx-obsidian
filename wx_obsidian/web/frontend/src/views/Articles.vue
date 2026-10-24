<script setup lang="ts">
/**
 * 文章管理（V2）：检索 + 筛选 + 排序 + 分页 + 详情抽屉 + 单条/批量级联删除。
 *
 * 状态分层：
 *  - **筛选/页码/每页条数/排序全部进 URL**（useUrlQueryState），刷新、后退、分享链接都不丢；
 *  - 服务端状态只有一份（useAsync 的 list），任何 URL 变化都由 queryKey 这一个 watch 触发重取，
 *    避免「改筛选」和「改页码」各写一条取数路径后互相打架；
 *  - 选中集合与当前页绑定：筛选/排序/翻页即清空（AppDataTable 的全选语义本来就是「本页全选」）。
 *
 * 旧版（Element Plus）行为逐条保留：关键词/分类/状态筛选、共 N 篇、在 Obsidian 打开、
 * 单条级联删除的确认文案与「清理 N 项」提示、20/50/100 分页。
 *
 * 交互改动（有意）：双击行打开 Obsidian → 单击行开详情抽屉、行内 ↵ 打开、按钮打开；
 * 浏览器 alert/confirm → AppDialog；新增列排序、多选批量重试/批量删除、详情抽屉。
 */
import { PhArrowClockwise, PhArticle, PhFunnelX, PhX } from '@phosphor-icons/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import ArticleDrawer from '@/components/domain/ArticleDrawer.vue'
import ArticleTable from '@/components/domain/ArticleTable.vue'
import BulkActionBar from '@/components/domain/BulkActionBar.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCombobox from '@/components/ui/AppCombobox.vue'
import AppCopyButton from '@/components/ui/AppCopyButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppEmptyState from '@/components/ui/AppEmptyState.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppPanel from '@/components/ui/AppPanel.vue'
import AppPagination from '@/components/ui/AppPagination.vue'
import AppSegmented from '@/components/ui/AppSegmented.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import { useUrlQueryState } from '@/composables/useUrlQueryState'
import { apiDelete, apiGet, apiPost, errorMessage, hasCode } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { ArticleItem, ArticleList, BulkDeleteResult, SortOrder } from '@/lib/api/types'
import { formatNumber, shortPath, truncate } from '@/lib/format'
import { useJobsStore } from '@/stores/jobs'

const PAGE_SIZES = [20, 50, 100]
/** 后端白名单（非法值会被忽略），这里再挡一层，避免 URL 被手改成乱序 */
const SORT_KEYS = ['processed_at', 'title', 'category'] as const
type SortKey = (typeof SORT_KEYS)[number]
const SORT_LABELS: Record<SortKey, string> = {
  processed_at: '处理时间',
  title: '标题',
  category: '分类',
}
const STATUS_OPTIONS = [
  { label: '全部', value: '' },
  { label: '已处理', value: 'done' },
  { label: '失败', value: 'error' },
  { label: '跳过', value: 'skipped' },
]

function isSortKey(value: string): value is SortKey {
  return (SORT_KEYS as readonly string[]).includes(value)
}

const router = useRouter()
const toast = useToast()
const jobs = useJobsStore()

// ── URL 即状态 ──────────────────────────────────────────────────────────────
const { state, patch } = useUrlQueryState({
  q: '',
  category: '',
  status: '',
  page: '1',
  size: '20',
  sort: '',
  order: '',
})

const page = computed(() => Math.max(1, Number(state.value.page) || 1))
const pageSize = computed(() => {
  const value = Number(state.value.size)
  return PAGE_SIZES.includes(value) ? value : 20
})
const searchTerm = computed(() => state.value.q.trim())
const categoryFilter = computed(() => state.value.category)
const statusFilter = computed(() => state.value.status)
const sortKey = computed<SortKey | ''>(() => (isSortKey(state.value.sort) ? state.value.sort : ''))
const sortOrder = computed<SortOrder>(() => (state.value.order === 'asc' ? 'asc' : 'desc'))
/** 只有「真的在筛」才算筛选：翻页/排序不该把空态切成「没有匹配」 */
const hasFilters = computed(() =>
  Boolean(searchTerm.value || categoryFilter.value || statusFilter.value),
)
const sortedByLabel = computed(() => (sortKey.value ? SORT_LABELS[sortKey.value] : '默认顺序'))

// ── 列表取数 ────────────────────────────────────────────────────────────────
const {
  data: listData,
  loading: listLoading,
  error: listError,
  run: runList,
} = useAsync<ArticleList>((signal) =>
  apiGet<ArticleList>(
    endpoints.articles.list({
      q: state.value.q,
      category: categoryFilter.value || undefined,
      status: statusFilter.value || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      // 默认不传 sort：保持后端既有顺序（新的在前）
      sort: sortKey.value || undefined,
      order: sortKey.value ? sortOrder.value : undefined,
    }),
    signal,
  ),
)

const rows = computed(() => listData.value?.items ?? [])
const categories = computed(() => listData.value?.categories ?? [])
const categoryOptions = computed(() => categories.value.map((name) => ({ label: name, value: name })))
const total = computed(() => listData.value?.total ?? 0)

const selected = ref<string[]>([])
const selectedRows = computed(() =>
  rows.value.filter((row) => selected.value.includes(row.article_id)),
)

const queryKey = computed(() =>
  [
    searchTerm.value,
    categoryFilter.value,
    statusFilter.value,
    page.value,
    pageSize.value,
    sortKey.value,
    sortOrder.value,
  ].join('|'),
)

watch(
  queryKey,
  () => {
    selected.value = []
    void runList()
  },
  { immediate: true },
)

/** 删到当前页空了（总数还有）就退回第一页，避免停在一个不存在的页码上 */
watch(rows, (value) => {
  const payload = listData.value
  if (payload && value.length === 0 && payload.total > 0 && page.value > 1) {
    patch({ page: '1' })
  }
})

/**
 * 手动刷新列表：读取失败由页内错误条说明（旧数据仍在时只降级提示），
 * 成功时给一次确认，避免「选了筛选条件但页面没变化」时以为点击无效。
 */
async function reload(): Promise<void> {
  const result = await runList()
  if (!result) {
    return
  }
  toast.success('刷新成功', `共 ${formatNumber(result.total)} 篇文章`)
}

// ── 关键词：250ms 防抖 + 回车即搜 ───────────────────────────────────────────
const searchText = ref(state.value.q)
let searchTimer: number | undefined

watch(
  () => state.value.q,
  (value) => {
    if (value !== searchText.value) {
      searchText.value = value
    }
  },
)

function commitSearch(): void {
  window.clearTimeout(searchTimer)
  if (state.value.q !== searchText.value || page.value !== 1) {
    patch({ q: searchText.value, page: '1' })
  }
}

function onSearchInput(value: string): void {
  searchText.value = value
  window.clearTimeout(searchTimer)
  if (value === '') {
    commitSearch()
    return
  }
  searchTimer = window.setTimeout(commitSearch, 250)
}

onBeforeUnmount(() => window.clearTimeout(searchTimer))

// ── 筛选栏动作 ──────────────────────────────────────────────────────────────
function setStatus(value: string): void {
  patch({ status: value, page: '1' })
}

function setCategory(value: string): void {
  patch({ category: value, page: '1' })
}

function resetFilters(): void {
  window.clearTimeout(searchTimer)
  searchText.value = ''
  // 只清筛选，不动每页条数与排序（那两个不是「筛选条件」）
  patch({ q: '', category: '', status: '', page: '1' })
}

function setPage(value: number): void {
  patch({ page: String(value) })
}

function setPageSize(value: number): void {
  patch({ size: String(value), page: '1' })
}

/** 点表头：同列切 asc/desc；换列按该列的默认方向起手（时间列先给最新） */
function setSort(key: string): void {
  if (!isSortKey(key)) {
    return
  }
  if (key === sortKey.value) {
    patch({ order: sortOrder.value === 'asc' ? 'desc' : 'asc', page: '1' })
    return
  }
  patch({ sort: key, order: key === 'processed_at' ? 'desc' : 'asc', page: '1' })
}

function clearSort(): void {
  patch({ sort: '', order: '', page: '1' })
}

function clearSelection(): void {
  selected.value = []
}

// ── 详情抽屉 ────────────────────────────────────────────────────────────────
const drawerOpen = ref(false)
const activeRow = ref<ArticleItem | null>(null)
const activeId = ref('')

const {
  data: detailData,
  loading: detailLoading,
  error: detailError,
  run: runDetail,
  reset: resetDetail,
} = useAsync<ArticleItem>((signal) =>
  apiGet<ArticleItem>(endpoints.articles.detail(activeId.value), signal),
)

const activeArticle = computed<ArticleItem | null>(() => detailData.value ?? activeRow.value)

function openDetail(row: ArticleItem): void {
  activeRow.value = row
  activeId.value = row.article_id
  drawerOpen.value = true
  // 换文章时先清掉上一篇的详情，避免抽屉里闪一下别人的错误详情
  resetDetail()
  void runDetail()
}

function reloadDetail(): void {
  void runDetail()
}

// ── 在 Obsidian 中打开（失败给完整的多行指引，不截断） ───────────────────────
const opening = ref(false)
const openError = ref('')
const openErrorHint = ref('')
const openErrorRow = ref<ArticleItem | null>(null)

async function openInObsidian(row: ArticleItem): Promise<void> {
  opening.value = true
  try {
    await apiPost(endpoints.articles.open(row.article_id))
    toast.success('已在 Obsidian 中打开', shortPath(row.file_path))
  } catch (err) {
    // 失败原因常常是多行可执行指引（例如「把该目录注册成仓库」），原文交给对话框完整展示
    openErrorRow.value = row
    openError.value = errorMessage(err)
    openErrorHint.value = hasCode(err, 'vault_not_registered')
      ? '照上面的指引做一遍（在 Obsidian 里打开过这个仓库）再重试。'
      : ''
  } finally {
    opening.value = false
  }
}

function dismissOpenError(): void {
  openError.value = ''
  openErrorHint.value = ''
  openErrorRow.value = null
}

function closeOpenError(open: boolean): void {
  if (!open) {
    dismissOpenError()
  }
}

async function retryOpen(): Promise<void> {
  const row = openErrorRow.value
  dismissOpenError()
  if (row) {
    await openInObsidian(row)
  }
}

function openActive(): void {
  const row = activeArticle.value
  if (row) {
    void openInObsidian(row)
  }
}

// ── 重试（单条 / 批量） ─────────────────────────────────────────────────────
const retrying = ref(false)

/** 返回是否真的提交成功：失败（例如 job_busy）时调用方要保住用户的选中集合 */
async function submitRetry(ids: string[]): Promise<boolean> {
  retrying.value = true
  try {
    await apiPost(endpoints.articles.retry(), { article_ids: ids })
    toast.success(`已提交重试任务：${formatNumber(ids.length)} 篇`, '进度见右下角任务坞')
    void jobs.refresh()
    return true
  } catch (err) {
    if (hasCode(err, 'job_busy')) {
      toast.warn('已有任务在运行，稍后再试', '重试要排队，等当前任务结束后再提交。')
    } else {
      toast.error(err, ids.length > 1 ? '批量重试失败' : '重试失败')
    }
    return false
  } finally {
    retrying.value = false
  }
}

async function bulkRetry(): Promise<void> {
  const ids = [...selected.value]
  if (!ids.length) {
    return
  }
  if (await submitRetry(ids)) {
    selected.value = []
    await runList()
  }
}

function retryActive(): void {
  const row = activeArticle.value
  if (row) {
    void submitRetry([row.article_id])
  }
}

// ── 删除（单条 / 批量） ─────────────────────────────────────────────────────
const deleting = ref(false)
const confirmRow = ref<ArticleItem | null>(null)
const bulkConfirmOpen = ref(false)
const bulkResult = ref<BulkDeleteResult | null>(null)

const bulkFailureOpen = computed(() => Boolean(bulkResult.value?.failed.length))
const confirmTitle = computed(() => (confirmRow.value ? truncate(confirmRow.value.title, 40) : ''))

function askRemove(row: ArticleItem): void {
  confirmRow.value = row
}

function cancelRemove(): void {
  if (!deleting.value) {
    confirmRow.value = null
  }
}

function askBulkRemove(): void {
  if (selected.value.length) {
    bulkConfirmOpen.value = true
  }
}

function removeActive(): void {
  const row = activeArticle.value
  if (row) {
    askRemove(row)
  }
}

async function confirmRemove(): Promise<void> {
  const row = confirmRow.value
  if (!row) {
    return
  }
  deleting.value = true
  try {
    const result = await apiDelete<{ ok: boolean; actions: string[] }>(
      endpoints.articles.remove(row.article_id),
    )
    toast.success('已删除文章', `清理了 ${formatNumber(result?.actions?.length ?? 0)} 项关联记录`)
    confirmRow.value = null
    if (activeId.value === row.article_id) {
      drawerOpen.value = false
    }
    selected.value = selected.value.filter((id) => id !== row.article_id)
    await runList()
  } catch (err) {
    toast.error(err, '删除失败')
  } finally {
    deleting.value = false
  }
}

async function bulkRemove(): Promise<void> {
  const ids = [...selected.value]
  if (!ids.length) {
    return
  }
  bulkConfirmOpen.value = false
  deleting.value = true
  try {
    const result = await apiPost<BulkDeleteResult>(endpoints.articles.bulkDelete(), {
      article_ids: ids,
    })
    bulkResult.value = result
    if (result.deleted > 0) {
      toast.success(
        `已删除 ${formatNumber(result.deleted)} 篇`,
        `清理 ${formatNumber(result.actions.length)} 项关联记录`,
      )
    }
    if (result.failed.length) {
      toast.warn(`${formatNumber(result.failed.length)} 篇没有删掉`, '逐条原因见结果面板')
    }
    selected.value = []
    if (ids.includes(activeId.value)) {
      drawerOpen.value = false
    }
    await runList()
  } catch (err) {
    if (hasCode(err, 'job_busy')) {
      toast.warn('已有任务在运行，稍后再试', '批量删除会与运行中的任务争抢记录写入，等它结束再试。')
    } else {
      toast.error(err, '批量删除失败')
    }
  } finally {
    deleting.value = false
  }
}

function closeBulkResult(open: boolean): void {
  if (!open) {
    bulkResult.value = null
  }
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <PageHeader
      title="文章管理"
      description="已处理文章的检索、详情与级联删除；筛选条件会同步到地址栏，可以直接分享当前视图。"
    >
      <template #actions>
        <p class="m-0 text-xs text-faint">
          共
          <span class="nums font-mono text-[13px] text-ink">{{ formatNumber(total) }}</span>
          篇
        </p>
      </template>
    </PageHeader>

    <!-- 筛选栏 -->
    <AppPanel padding="sm" as="div" class="flex flex-col gap-2.5">
      <div class="flex flex-wrap items-center gap-2.5">
        <div class="min-w-[220px] max-w-[420px] flex-1">
          <AppInput
            :model-value="searchText"
            clearable
            placeholder="搜索标题（/ 聚焦，↵ 立即搜）"
            @update:model-value="onSearchInput"
            @enter="commitSearch"
          />
        </div>

        <div class="w-full sm:w-[220px]">
          <AppCombobox
            :model-value="categoryFilter"
            :options="categoryOptions"
            all-label="全部分类"
            placeholder="全部分类"
            empty-text="没有匹配的分类"
            @update:model-value="setCategory"
          />
        </div>

        <AppSegmented
          :model-value="statusFilter"
          :options="STATUS_OPTIONS"
          @update:model-value="setStatus"
        />

        <div class="ml-auto flex items-center gap-2">
          <AppButton size="sm" variant="outline" @click="resetFilters">重置</AppButton>
          <AppButton size="sm" variant="ghost" :loading="listLoading" @click="reload">
            <template #icon>
              <PhArrowClockwise :size="14" aria-hidden="true" />
            </template>
            刷新
          </AppButton>
        </div>
      </div>

      <div
        class="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-hairline pt-2.5 text-xs text-faint"
      >
        <span>
          排序：<span class="text-muted">{{ sortedByLabel }}</span>
          <span v-if="sortKey" class="nums ml-1 font-mono">
            {{ sortOrder === 'asc' ? '升序' : '降序' }}
          </span>
        </span>
        <AppButton v-if="sortKey" size="sm" variant="ghost" @click="clearSort">
          <template #icon>
            <PhX :size="12" aria-hidden="true" />
          </template>
          清除排序
        </AppButton>
      </div>
    </AppPanel>

    <!-- 表格 + 批量条 + 分页 -->
    <AppPanel padding="none" as="div">
      <BulkActionBar
        v-if="selected.length"
        :count="selected.length"
        :retrying="retrying"
        :deleting="deleting"
        :disabled="retrying || deleting"
        @retry="bulkRetry"
        @remove="askBulkRemove"
        @clear="clearSelection"
      />

      <!-- 刷新失败但手里还有上次的数据：降级成一条错误条，不把表格换掉 -->
      <AppErrorState
        v-if="listError && rows.length"
        class="m-3"
        compact
        :message="listError"
        hint="下面是上一次成功加载的数据，重试即可刷新。"
        @retry="reload"
      />

      <AppErrorState
        v-if="listError && !rows.length && !listLoading"
        class="m-3"
        :message="listError"
        hint="列表读不出来时，先确认本地服务还在运行。"
        @retry="reload"
      />

      <ArticleTable
        v-else
        :rows="rows"
        :loading="listLoading"
        :selected="selected"
        :sort-key="sortKey"
        :sort-order="sortOrder"
        :skeleton-rows="pageSize"
        :busy="retrying || deleting"
        @update:selected="selected = $event"
        @sort="setSort"
        @row-click="openDetail"
        @row-enter="openInObsidian"
        @open="openInObsidian"
        @remove="askRemove"
      >
        <template #empty>
          <!-- 空态分两种：筛出来是空的 ≠ 压根还没处理过文章 -->
          <AppEmptyState
            v-if="hasFilters"
            :icon="PhFunnelX"
            title="没有符合条件的文章"
            description="换个关键词，或把分类、状态筛选清空再看。"
          >
            <template #action>
              <AppButton size="sm" variant="outline" @click="resetFilters">清空筛选</AppButton>
            </template>
          </AppEmptyState>

          <AppEmptyState
            v-else
            :icon="PhArticle"
            title="还没有处理过文章"
            description="去「文章抓取」跑一轮：抓取并生成笔记后，文章会连同分类与处理状态出现在这里。"
          >
            <template #action>
              <AppButton size="sm" variant="solid" @click="router.push('/run')">去文章抓取</AppButton>
            </template>
          </AppEmptyState>
        </template>
      </ArticleTable>

      <!-- 表格最后一行已带行线，这里只在没有行时补一条，避免出现双发丝线 -->
      <div v-if="total > 0" :class="rows.length ? '' : 'border-t border-hairline'">
        <AppPagination
          :page="page"
          :page-size="pageSize"
          :total="total"
          :page-sizes="PAGE_SIZES"
          :disabled="listLoading"
          @update:page="setPage"
          @update:page-size="setPageSize"
        />
      </div>
    </AppPanel>

    <!-- 详情抽屉 -->
    <ArticleDrawer
      v-model:open="drawerOpen"
      :article="activeRow"
      :detail="detailData"
      :loading="detailLoading"
      :error="detailError"
      :opening="opening"
      :retrying="retrying"
      :deleting="deleting"
      @open="openActive"
      @retry="retryActive"
      @remove="removeActive"
      @reload="reloadDetail"
    />

    <!-- 单条级联删除确认：要点文案原样保留 -->
    <AppDialog
      :open="confirmRow !== null"
      title="级联删除"
      description="删除后无法撤销"
      width="sm"
      @update:open="cancelRemove"
    >
      <p class="m-0 text-[13.5px] leading-relaxed text-ink">
        确定删除《{{ confirmTitle }}》？关联的 MOC 条目、概念页引用与归档记录会一并清理。
      </p>
      <p v-if="confirmRow?.file_path" class="m-0 mt-2 font-mono text-xs break-all text-faint">
        {{ shortPath(confirmRow.file_path) }}
      </p>
      <template #footer>
        <AppButton size="sm" variant="ghost" :disabled="deleting" @click="cancelRemove">取消</AppButton>
        <AppButton size="sm" variant="danger" :loading="deleting" @click="confirmRemove">删除</AppButton>
      </template>
    </AppDialog>

    <!-- 批量删除确认 -->
    <AppDialog
      :open="bulkConfirmOpen"
      title="批量级联删除"
      :description="`已选 ${formatNumber(selected.length)} 篇`"
      width="md"
      @update:open="bulkConfirmOpen = $event"
    >
      <p class="m-0 text-[13.5px] leading-relaxed text-ink">
        确定删除选中的 {{ formatNumber(selected.length) }} 篇文章？关联的 MOC
        条目、概念页引用与归档记录会一并清理。
      </p>
      <ul v-if="selectedRows.length" class="m-0 mt-3 flex list-none flex-col gap-1.5 p-0">
        <li
          v-for="row in selectedRows.slice(0, 6)"
          :key="row.article_id"
          class="truncate text-[13px] text-muted"
        >
          {{ row.title }}
        </li>
        <li v-if="selectedRows.length > 6" class="text-xs text-faint">
          等共 {{ formatNumber(selectedRows.length) }} 篇
        </li>
      </ul>
      <template #footer>
        <AppButton size="sm" variant="ghost" @click="bulkConfirmOpen = false">取消</AppButton>
        <AppButton size="sm" variant="danger" :loading="deleting" @click="bulkRemove">
          删除 {{ formatNumber(selected.length) }} 篇
        </AppButton>
      </template>
    </AppDialog>

    <!-- 打开失败：后端返回的多行可执行指引完整展示，不截断、不弹浏览器 alert -->
    <AppDialog
      :open="Boolean(openError)"
      title="无法在 Obsidian 中打开"
      description="下面是后端的原文指引"
      width="lg"
      @update:open="closeOpenError"
    >
      <AppErrorState
        :message="openError"
        :hint="openErrorHint"
        retry-label="重试打开"
        @retry="retryOpen"
      />
      <p class="m-0 mt-3 text-xs leading-relaxed text-faint">
        最常见的原因：这篇笔记所在的目录还不是 Obsidian 已注册的仓库——先用 Obsidian
        打开一次那个目录，再回来重试。
      </p>
      <template #footer>
        <AppCopyButton :value="openError" label="复制报错原文" variant="outline" />
        <AppButton size="sm" variant="ghost" :disabled="opening" @click="dismissOpenError">
          关闭
        </AppButton>
      </template>
    </AppDialog>

    <!-- 批量删除的部分失败汇总 -->
    <AppDialog
      :open="bulkFailureOpen"
      title="批量删除结果"
      :description="
        bulkResult
          ? `已删除 ${formatNumber(bulkResult.deleted)} 篇 · 清理 ${formatNumber(bulkResult.actions.length)} 项关联记录`
          : ''
      "
      width="lg"
      @update:open="closeBulkResult"
    >
      <p class="m-0 text-[13px] text-muted">
        {{ formatNumber(bulkResult?.failed.length ?? 0) }} 篇没有删除成功，逐条原因如下：
      </p>
      <ul class="m-0 mt-3 flex list-none flex-col gap-2 p-0">
        <li
          v-for="item in bulkResult?.failed ?? []"
          :key="item.article_id"
          class="rounded-control border border-hairline bg-inset px-3 py-2"
        >
          <p class="m-0 font-mono nums text-[11.5px] break-all text-faint">{{ item.article_id }}</p>
          <p class="m-0 mt-1 text-[13px] leading-relaxed whitespace-pre-line text-ink">
            {{ item.error }}
          </p>
        </li>
      </ul>
      <p class="m-0 mt-3 text-xs leading-relaxed text-faint">
        提示：article_not_found 表示这条记录已经被清理过，刷新列表后它就不会再出现。
      </p>
      <template #footer>
        <AppButton size="sm" variant="outline" @click="reload">刷新列表</AppButton>
        <AppButton size="sm" variant="ghost" @click="bulkResult = null">关闭</AppButton>
      </template>
    </AppDialog>
  </div>
</template>
