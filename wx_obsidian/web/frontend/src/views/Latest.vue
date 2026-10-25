<script setup lang="ts">
/**
 * 最新文章：按发布时间浏览数据源文章库里的全部订阅文章，点击行在新标签页打开原文。
 *
 * 取数分两条独立通道：
 *  - 文章第一页与刷新走 useAsync，骨架 / 错误 / 空态由它统一给出；
 *  - 公众号列表只用于筛选，取不到时只留「全部公众号」一个选项，不阻断文章列表。
 *
 * 上游失败是 200 + {error, detail}，必须判形状：否则空对象会被当成「没有文章」，
 * 等于把令牌失效说成「没有内容」。
 *
 * 没有正文的文章按 `content_state` 分成补抓中 / 待补抓 / 补抓失败三种标签；
 * 待补抓与补抓失败给「重试」，触发成功后延时重拉，补抓进度条读 feed-sync store。
 */
import {
  PhArrowClockwise,
  PhArrowSquareOut,
  PhCloudArrowDown,
  PhFunnelX,
  PhKey,
  PhNewspaper,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import AppButton from '@/components/ui/AppButton.vue'
import AppEmptyState from '@/components/ui/AppEmptyState.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppProgress from '@/components/ui/AppProgress.vue'
import AppSelect, { type SelectOption } from '@/components/ui/AppSelect.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppTag from '@/components/ui/AppTag.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import { ApiError, apiGet, apiPost, errorMessage } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type {
  ArticleRefreshResult,
  FeedItem,
  SourceArticleItem,
  SourceArticleListError,
  SourceArticlePage,
} from '@/lib/api/types'
import { groupByPublishedDay } from '@/lib/article-timeline'
import { backfillPercent, contentStateMeta, needsContentRetry } from '@/lib/content-state'
import { formatDateTime, formatNumber, formatRelative, initialOf, stableHue } from '@/lib/format'
import { useFeedSyncStore } from '@/stores/feed-sync'

/** 单页条数：一次 30 条，继续往下靠「加载更多」 */
const PAGE_SIZE = 30
/** AppSelect 的选项值不允许空串，用固定哨兵表示「全部公众号」 */
const ALL_FEEDS = '__all__'

const router = useRouter()
const sync = useFeedSyncStore()
const toast = useToast()

const feedFilter = ref(ALL_FEEDS)
const selectedFeedId = computed(() => (feedFilter.value === ALL_FEEDS ? '' : feedFilter.value))

/** 本次首屏 / 刷新拿到的失败体：用来区分令牌失效与上游不可达 */
const failure = ref<SourceArticleListError | null>(null)
/** 「加载更多」追加的后续页 */
const moreItems = ref<SourceArticleItem[]>([])
const loadingMore = ref(false)
const loadMoreError = ref('')
/** 上游返回空页：即使 total 还没到也不该继续请求同一偏移 */
const exhausted = ref(false)
const failedCovers = ref<string[]>([])

const { data: feedData, run: loadFeeds } = useAsync<FeedItem[]>(
  async (signal) => {
    const payload = await apiGet<FeedItem[] | SourceArticleListError>(
      endpoints.feeds.list(),
      signal,
    )
    // /api/feeds 失败时是 200 + {error, detail}：只保留「全部公众号」，不阻断文章列表
    return Array.isArray(payload) ? payload : []
  },
  { immediate: true },
)

const {
  data: pageData,
  loading,
  error: listError,
  run: loadFirstPage,
  reset: resetFirstPage,
} = useAsync<SourceArticlePage>(
  async (signal) => {
    failure.value = null
    const payload = await apiGet<SourceArticlePage | SourceArticleListError>(
      endpoints.source.articles({
        offset: 0,
        limit: PAGE_SIZE,
        mp_id: selectedFeedId.value,
      }),
      signal,
    )
    if (!Array.isArray((payload as SourceArticlePage).items)) {
      const body = payload as SourceArticleListError
      failure.value = body
      throw new ApiError(200, body.detail || '读取文章列表失败', body.error)
    }
    return payload as SourceArticlePage
  },
  { immediate: true },
)

const items = computed<SourceArticleItem[]>(() => [
  ...(pageData.value?.items ?? []),
  ...moreItems.value,
])
const total = computed(() => pageData.value?.total ?? 0)
const groups = computed(() => groupByPublishedDay(items.value))
const hasItems = computed(() => items.value.length > 0)
const hasMore = computed(() => !exhausted.value && items.value.length < total.value)
const tokenMissing = computed(() => failure.value?.error === 'source_token_invalid')
const staleHint = computed(() =>
  tokenMissing.value
    ? '列表是上一次成功加载的结果；令牌修好后点「重试」。'
    : '列表是上一次成功加载的结果，点「重试」刷新。',
)
const feedOptions = computed<SelectOption[]>(() => [
  { label: '全部公众号', value: ALL_FEEDS },
  ...(feedData.value ?? []).map((feed) => ({ label: feed.name || feed.id, value: feed.id })),
])

/**
 * 自动采集配置、采集与正文补抓的进度全部取自 feed-sync store 的同一轮轮询：
 * 页面不再另起定时器，补抓条跟着右下角进度卡的数据走。
 */
const collecting = ref(false)
let reloadTimer: ReturnType<typeof setTimeout> | null = null
/** 补抓触发后的延时重拉定时器（5 秒、25 秒各一次） */
const retryTimers: ReturnType<typeof setTimeout>[] = []
/** 正在触发补抓的文章 id：按钮进入 loading，避免连点 */
const retrying = ref<string[]>([])

/** 关闭自动采集时后端返回 0，这里给一个显示用的默认值 */
const FALLBACK_SYNC_MINUTES = 30
const syncIntervalMinutes = computed(
  () => sync.data?.auto_interval_minutes || FALLBACK_SYNC_MINUTES,
)
const staleThresholdMinutes = computed(() => Math.max(60, syncIntervalMinutes.value * 2))

/** 正文补抓进度：本轮开始时待补抓篇数、已触发篇数、触发失败篇数 */
const backfillRunning = computed(() => Boolean(sync.data?.backfill_running))
const backfillPending = computed(() => sync.data?.backfill_pending ?? 0)
const backfillRefreshed = computed(() => sync.data?.backfill_refreshed ?? 0)
const backfillFailed = computed(() => sync.data?.backfill_failed ?? 0)
/** 补抓被令牌失效等硬错误中止的原因；空串不渲染 */
const backfillError = computed(() => sync.data?.backfill_error || '')
/** 三项计数全为 0 时补抓执行器还在等新文章入库，此时不显示进度条 */
const backfillHasWork = computed(
  () => backfillPending.value + backfillRefreshed.value + backfillFailed.value > 0,
)
const backfillPercentValue = computed(() =>
  backfillPercent(backfillPending.value, backfillRefreshed.value),
)

/** 数据源最近一次采集时间：取所有订阅里最新的 sync_time */
const lastSyncAt = computed(() => {
  const times = (feedData.value ?? [])
    .map((feed) => feed.sync_time)
    .filter((value): value is string => Boolean(value))
    .map((value) => new Date(value).getTime())
    .filter((value) => Number.isFinite(value))
  return times.length > 0 ? new Date(Math.max(...times)).toISOString() : ''
})
const lastSyncLabel = computed(() =>
  lastSyncAt.value ? formatRelative(lastSyncAt.value) : '未知',
)
const dataStale = computed(() => {
  if (!lastSyncAt.value) {
    return false
  }
  const ageMinutes = (Date.now() - new Date(lastSyncAt.value).getTime()) / 60000
  return ageMinutes > staleThresholdMinutes.value
})

async function loadSyncStatus(): Promise<void> {
  // 采集结束后紧接着补抓正文：store 的轮询会一路跟着，进度条不会中途断掉
  await sync.refresh()
}

/** 采集结束后等上游最后一轮落库，再刷新列表 */
function scheduleListReload(): void {
  if (reloadTimer !== null) {
    clearTimeout(reloadTimer)
  }
  reloadTimer = setTimeout(() => {
    reloadTimer = null
    collecting.value = false
    void refresh()
    void loadSyncStatus()
  }, 10000)
}

async function startCollect(): Promise<void> {
  if (collecting.value) {
    return
  }
  collecting.value = true
  const started = await sync.trigger()
  if (!started) {
    collecting.value = false
    return
  }
  void loadSyncStatus()
  if (!sync.running) {
    scheduleListReload()
  }
}

watch(
  () => sync.finishedTick,
  () => {
    scheduleListReload()
  },
)

onUnmounted(() => {
  if (reloadTimer !== null) {
    clearTimeout(reloadTimer)
    reloadTimer = null
  }
  clearRetryTimers()
})

void loadSyncStatus()

watch(selectedFeedId, () => {
  failure.value = null
  moreItems.value = []
  loadMoreError.value = ''
  exhausted.value = false
  failedCovers.value = []
  resetFirstPage()
  void loadFirstPage()
})

async function refresh(): Promise<void> {
  moreItems.value = []
  loadMoreError.value = ''
  exhausted.value = false
  failedCovers.value = []
  await Promise.all([loadFirstPage(), loadFeeds()])
}

/**
 * 手动点「刷新」：采集结束后的自动刷新仍走 refresh()，不弹提示。
 * 读取失败由页内错误块说明（令牌失效与上游不可达各有文案），这里只补成功反馈。
 */
async function manualRefresh(): Promise<void> {
  await refresh()
  if (listError.value || !pageData.value) {
    // 令牌失效时页面只换空态文案；再给一条警告，点过按钮才有明确回音
    if (tokenMissing.value) {
      toast.warn('刷新完成，但数据源令牌无效', '到「配置管理 → 文章数据源」更新访问令牌后再刷新')
    }
    return
  }
  toast.success('刷新成功', `共 ${formatNumber(total.value)} 篇文章`)
}

async function loadMore(): Promise<void> {
  if (loadingMore.value || !hasMore.value) {
    return
  }
  loadingMore.value = true
  loadMoreError.value = ''
  try {
    const payload = await apiGet<SourceArticlePage | SourceArticleListError>(
      endpoints.source.articles({
        offset: items.value.length,
        limit: PAGE_SIZE,
        mp_id: selectedFeedId.value,
      }),
    )
    if (!Array.isArray((payload as SourceArticlePage).items)) {
      const body = payload as SourceArticleListError
      throw new ApiError(200, body.detail || '加载更多失败', body.error)
    }
    const page = payload as SourceArticlePage
    if (page.items.length === 0) {
      exhausted.value = true
      return
    }
    moreItems.value = [...moreItems.value, ...page.items]
  } catch (err) {
    loadMoreError.value = errorMessage(err)
  } finally {
    loadingMore.value = false
  }
}

function setFeedFilter(value: string): void {
  feedFilter.value = value
}

/** 无正文的三种状态标签；`ready` 返回 null，不渲染标签 */
function contentTag(item: SourceArticleItem) {
  return contentStateMeta(item.content_state)
}

/** 待补抓与补抓失败的行才给「重试」 */
function canRetryContent(item: SourceArticleItem): boolean {
  return needsContentRetry(item.content_state)
}

function clearRetryTimers(): void {
  retryTimers.splice(0).forEach((timer) => clearTimeout(timer))
}

/**
 * 补抓触发成功后延时重拉：后端补抓要几秒到几十秒，分 5 秒 / 25 秒两档观察。
 * 每次重试都重置这两个定时器，避免连点后堆一串请求；组件卸载时统一清空。
 */
function scheduleRetryReload(): void {
  clearRetryTimers()
  retryTimers.push(
    setTimeout(() => {
      void refresh()
      void loadSyncStatus()
    }, 5000),
    setTimeout(() => {
      void refresh()
      void loadSyncStatus()
    }, 25000),
  )
}

/** 单篇文章补抓：triggered/skipped 都算成功，failed 把原因交给 toast.error */
async function retryContent(item: SourceArticleItem): Promise<void> {
  if (retrying.value.includes(item.article_id)) {
    return
  }
  retrying.value = [...retrying.value, item.article_id]
  try {
    // 上游单篇补抓只认内部 ID：article_id 是归一化后的短链 token，传它会 404
    const result = await apiPost<ArticleRefreshResult>(
      endpoints.source.articlesRefresh(item.upstream_id),
    )
    if (!result.ok || result.outcome === 'failed') {
      toast.error(result.message || '补抓触发失败', '稍后可再点「重试」')
      return
    }
    toast.success(
      result.outcome === 'triggered' ? '已开始补抓正文' : '补抓任务已在执行',
      result.message || '正文补好后列表会自动更新',
    )
    scheduleRetryReload()
    void loadSyncStatus()
  } catch (err) {
    toast.error(err, '补抓触发失败')
  } finally {
    retrying.value = retrying.value.filter((id) => id !== item.article_id)
  }
}

function resetFeedFilter(): void {
  feedFilter.value = ALL_FEEDS
}

function markCoverFailed(id: string): void {
  if (!failedCovers.value.includes(id)) {
    failedCovers.value = [...failedCovers.value, id]
  }
}

function showCover(item: SourceArticleItem): boolean {
  return Boolean(item.cover) && !failedCovers.value.includes(item.article_id)
}

/** 无封面或封面加载失败时的占位块：色相由名字稳定推导，同名永远同色 */
function placeholderStyle(name: string): Record<string, string> {
  return {
    backgroundColor: 'color-mix(in srgb, var(--accent) 18%, var(--surface-raised))',
    filter: `hue-rotate(${stableHue(name)}deg)`,
  }
}

function coverInitial(item: SourceArticleItem): string {
  return initialOf(item.account || item.title)
}
</script>

<template>
  <div>
    <PageHeader
      title="最新文章"
      description="所有订阅公众号的文章，按发布时间倒序；点任意一条在新标签页打开原文。"
    >
      <template #meta>
        <div
          v-if="hasItems"
          class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-faint"
        >
          <span class="nums font-mono">共 {{ formatNumber(total) }} 篇</span>
          <span class="nums font-mono">已显示 {{ formatNumber(items.length) }} 篇</span>
          <span class="nums font-mono">数据源最近采集 {{ lastSyncLabel }}</span>
          <span v-if="dataStale" class="text-warning">
            超过 {{ staleThresholdMinutes }} 分钟未采集，点「立即采集」更新
          </span>
        </div>
      </template>

      <template #actions>
        <div class="w-[180px]">
          <AppSelect
            :model-value="feedFilter"
            :options="feedOptions"
            size="sm"
            placeholder="全部公众号"
            @update:model-value="setFeedFilter"
          />
        </div>
        <AppButton
          variant="solid"
          size="sm"
          :loading="collecting"
          :disabled="collecting"
          @click="startCollect"
        >
          <template #icon>
            <PhCloudArrowDown :size="14" aria-hidden="true" />
          </template>
          立即采集
        </AppButton>
        <AppButton variant="outline" size="sm" :loading="loading" @click="manualRefresh">
          <template #icon>
            <PhArrowClockwise :size="14" aria-hidden="true" />
          </template>
          刷新
        </AppButton>
      </template>
    </PageHeader>

    <!-- 正文补抓进度与中止原因：采集结束后后端自动补抓，数据与右下角进度卡共用 -->
    <section
      v-if="backfillError || (backfillRunning && backfillHasWork)"
      class="panel mb-5 rounded-panel px-4 py-3.5 sm:px-5"
      aria-label="正文补抓进度"
      role="status"
    >
      <template v-if="backfillRunning && backfillHasWork">
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <p class="m-0 text-[13px] leading-snug font-medium text-ink">
            正在补充正文：待补抓 {{ formatNumber(backfillPending) }} 篇，已触发
            {{ formatNumber(backfillRefreshed) }} 篇
          </p>
          <span v-if="backfillFailed > 0" class="nums font-mono text-[11.5px] text-warning">
            {{ formatNumber(backfillFailed) }} 篇触发失败
          </span>
        </div>
        <AppProgress class="mt-2.5" :value="backfillPercentValue" tone="accent" size="sm" />
      </template>
      <p
        v-if="backfillError"
        class="m-0 flex items-start gap-2 text-xs leading-relaxed text-danger"
        :class="backfillRunning ? 'mt-2.5' : ''"
      >
        <PhWarningCircle :size="14" class="mt-px shrink-0" aria-hidden="true" />
        <span>补抓未能继续：{{ backfillError }}</span>
      </p>
    </section>

    <AppEmptyState
      v-if="tokenMissing && !hasItems"
      :icon="PhKey"
      title="读不到文章：数据源访问令牌未配置或已失效"
      description="文章列表来自数据源管理接口，需要 Bearer Token；未配置或过期时上游只回空结果。"
    >
      <template #hint>
        <p class="m-0 mt-2 max-w-[56ch] text-xs leading-relaxed text-faint">
          在「配置管理 → 文章数据源 → 访问令牌」填入并保存，回到本页点「重试」。
        </p>
      </template>
      <template #action>
        <AppButton variant="tonal" size="sm" @click="router.push('/settings')">
          去配置访问令牌
        </AppButton>
      </template>
    </AppEmptyState>

    <AppErrorState
      v-else-if="listError && !hasItems && !loading"
      :message="listError"
      hint="上游不可达时先去「数据源状态」看探活结果；服务恢复后点「重试」。"
      @retry="manualRefresh"
    />

    <div v-else-if="loading && !hasItems" class="flex flex-col gap-2.5">
      <div
        v-for="index in 5"
        :key="index"
        class="panel flex items-center gap-4 rounded-panel px-4 py-3.5 sm:px-5"
      >
        <AppSkeleton variant="block" width="96px" height="64px" />
        <div class="flex min-w-0 flex-1 flex-col gap-2">
          <AppSkeleton variant="line" width="72%" />
          <AppSkeleton variant="line" width="38%" />
        </div>
      </div>
    </div>

    <AppEmptyState
      v-else-if="!hasItems"
      :icon="feedFilter === ALL_FEEDS ? PhNewspaper : PhFunnelX"
      :title="feedFilter === ALL_FEEDS ? '还没有可取的文章' : '这个公众号还没有文章'"
      :description="
        feedFilter === ALL_FEEDS
          ? '点右上角「立即采集」拉取订阅公众号的最新文章。'
          : '换一个公众号，或切回「全部公众号」查看所有订阅内容。'
      "
    >
      <template #action>
        <AppButton
          v-if="feedFilter === ALL_FEEDS"
          variant="solid"
          size="sm"
          :loading="collecting"
          @click="startCollect"
        >
          立即采集
        </AppButton>
        <AppButton v-else variant="outline" size="sm" @click="resetFeedFilter">
          查看全部公众号
        </AppButton>
      </template>
    </AppEmptyState>

    <template v-else>
      <AppErrorState
        v-if="listError"
        class="mb-4"
        compact
        :message="listError"
        :hint="staleHint"
        @retry="manualRefresh"
      />

      <section v-for="group in groups" :key="group.key" class="mb-6 last:mb-0">
        <header class="mb-2 flex items-baseline justify-between gap-3 px-1">
          <h2 class="m-0 text-[13px] font-semibold text-ink">{{ group.label }}</h2>
          <span class="nums font-mono text-[11px] text-faint">{{ group.items.length }} 篇</span>
        </header>

        <ul class="panel m-0 list-none overflow-hidden rounded-panel p-0">
          <li
            v-for="item in group.items"
            :key="item.article_id"
            class="border-t border-hairline first:border-t-0"
          >
            <div
              class="group row-hover flex items-start gap-3.5 px-4 py-3.5 hover:bg-raised/40 sm:px-5"
            >
              <a
                :href="item.url"
                target="_blank"
                rel="noopener noreferrer"
                class="flex min-w-0 flex-1 items-start gap-3.5"
              >
                <div
                  class="hidden h-16 w-24 shrink-0 overflow-hidden rounded-[10px] border border-hairline bg-inset sm:block"
                >
                  <img
                    v-if="showCover(item)"
                    :src="item.cover"
                    :alt="`${item.account || item.title} 封面`"
                    class="size-full object-cover"
                    loading="lazy"
                    referrerpolicy="no-referrer"
                    @error="markCoverFailed(item.article_id)"
                  />
                  <div
                    v-else
                    class="grid size-full place-items-center text-[18px] font-medium text-muted"
                    :style="placeholderStyle(item.account || item.title)"
                    aria-hidden="true"
                  >
                    {{ coverInitial(item) }}
                  </div>
                </div>

                <div class="min-w-0 flex-1">
                  <p
                    class="m-0 line-clamp-2 text-[14px] leading-snug font-medium text-ink transition-colors duration-150 group-hover:text-accent"
                  >
                    {{ item.title }}
                  </p>
                  <div
                    class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-faint"
                  >
                    <span class="max-w-[32ch] truncate text-muted">
                      {{ item.account || '未知公众号' }}
                    </span>
                    <AppTooltip v-if="item.published_at" :content="formatDateTime(item.published_at)">
                      <span class="nums cursor-default font-mono">
                        {{ formatRelative(item.published_at) }}
                      </span>
                    </AppTooltip>
                    <AppTag v-if="item.processed" tone="success" variant="soft">已入库</AppTag>
                    <AppTag
                      v-if="contentTag(item)"
                      :tone="contentTag(item)?.tone"
                      :variant="contentTag(item)?.variant"
                    >
                      {{ contentTag(item)?.label }}
                    </AppTag>
                    <span v-if="item.content_state === 'failed'" class="nums font-mono">
                      已试 {{ formatNumber(item.content_attempts) }} 次
                    </span>
                  </div>
                </div>
              </a>

              <div class="flex shrink-0 flex-col items-end gap-2">
                <AppButton
                  v-if="canRetryContent(item)"
                  variant="outline"
                  size="sm"
                  :loading="retrying.includes(item.article_id)"
                  @click="retryContent(item)"
                >
                  重试
                </AppButton>
                <PhArrowSquareOut
                  :size="15"
                  class="mt-0.5 text-faint transition-colors duration-150 group-hover:text-accent"
                  aria-hidden="true"
                />
              </div>
            </div>
          </li>
        </ul>
      </section>

      <div class="mt-5 flex flex-col items-center gap-3">
        <AppErrorState
          v-if="loadMoreError"
          compact
          class="w-full"
          :message="loadMoreError"
          hint="已加载的内容不受影响，点「重试」继续往下取。"
          @retry="loadMore"
        />
        <p class="m-0 nums font-mono text-[11.5px] text-faint">
          已显示 {{ formatNumber(items.length) }} / 共 {{ formatNumber(total) }} 篇
        </p>
        <AppButton v-if="hasMore" variant="outline" :loading="loadingMore" @click="loadMore">
          加载更多
        </AppButton>
      </div>
    </template>
  </div>
</template>
