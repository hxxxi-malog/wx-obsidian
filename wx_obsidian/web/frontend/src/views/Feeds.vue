<script setup lang="ts">
/**
 * 公众号管理：订阅列表 + 按文章链接添加。
 *
 * 为什么必须区分两种空态：上游订阅接口（/api/v1/wx/mps）要 Bearer Token，
 * 没配令牌时它只会返回空列表——这时候显示「还没有订阅任何公众号」是在撒谎，
 * 用户会以为自己的订阅丢了。
 */
import { PhArrowClockwise, PhKey, PhPlus, PhRss, PhTrash } from '@phosphor-icons/vue'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppButton from '@/components/ui/AppButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppEmptyState from '@/components/ui/AppEmptyState.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import { ApiError, apiDelete, apiGet, apiPost, hasCode } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { ConfigSnapshot, FeedItem } from '@/lib/api/types'
import { formatDateTime, formatRelative, initialOf, stableHue } from '@/lib/format'

const router = useRouter()
const toast = useToast()

interface FeedsPayload {
  feeds: FeedItem[]
  /** 数据源访问令牌是否已配置：决定空态该说哪句话 */
  tokenConfigured: boolean
}

const state = useAsync<FeedsPayload>(
  async (signal) => {
    const [list, config] = await Promise.all([
      apiGet<FeedItem[]>(endpoints.feeds.list(), signal),
      apiGet<ConfigSnapshot>(endpoints.config.get(), signal),
    ])
    // 后端在数据源不可达时是 200 + {error, detail}，不是 4xx：
    // 不判形状的话，非数组会被当成「空列表」渲染，等于把故障说成「没有订阅」。
    if (!Array.isArray(list)) {
      throw new ApiError(200, '数据源没有返回订阅列表（可能不可达或令牌失效）', 'feeds_unavailable')
    }
    return { feeds: list, tokenConfigured: Boolean(config.source.token) }
  },
  { immediate: true },
)

const feeds = computed(() => state.data.value?.feeds ?? [])
const tokenConfigured = computed(() => state.data.value?.tokenConfigured ?? true)
const showEmpty = computed(() => !state.loading.value && !state.error.value && feeds.value.length === 0)

const articleUrl = ref('')
const adding = ref(false)
const confirmFeed = ref<FeedItem | null>(null)
const removing = ref(false)
const failedCovers = ref<string[]>([])

/** 软校验：不是公众号文章链接也照样提交（数据源可能支持别的形态），只是先提醒一句 */
const urlHint = computed(() => {
  const url = articleUrl.value.trim()
  if (!url || /^https?:\/\/mp\.weixin\.qq\.com\/s\//i.test(url)) {
    return ''
  }
  return '看起来不是公众号文章链接（通常形如 https://mp.weixin.qq.com/s/…），仍会尝试添加'
})

function markCoverFailed(id: string): void {
  if (!failedCovers.value.includes(id)) {
    failedCovers.value = [...failedCovers.value, id]
  }
}

function showCover(feed: FeedItem): boolean {
  return Boolean(feed.cover) && !failedCovers.value.includes(feed.id)
}

/**
 * 无封面（或封面加载失败）时的占位块：色相由名字稳定推导，同名永远同色；
 * 颜色只用 token 混出来，不写裸色值。
 */
function placeholderStyle(name: string): Record<string, string> {
  return {
    backgroundColor: 'color-mix(in srgb, var(--accent) 18%, var(--surface-raised))',
    filter: `hue-rotate(${stableHue(name)}deg)`,
  }
}

async function add(): Promise<void> {
  const url = articleUrl.value.trim()
  if (!url) {
    toast.warn('请先粘贴一篇文章链接')
    return
  }
  adding.value = true
  try {
    const feed = await apiPost<FeedItem>(endpoints.feeds.add(), { article_url: url })
    toast.success(`已添加：${feed.name}`)
    articleUrl.value = ''
    await state.run()
  } catch (err) {
    if (hasCode(err, 'not_supported')) {
      toast.warn('当前数据源不支持按文章链接添加公众号', '请在 we-mp-rss 界面里订阅')
    } else {
      toast.error(err)
    }
  } finally {
    adding.value = false
  }
}

async function remove(): Promise<void> {
  const feed = confirmFeed.value
  if (!feed) {
    return
  }
  removing.value = true
  try {
    await apiDelete(endpoints.feeds.remove(feed.id))
    toast.success(`已取消订阅：${feed.name}`)
    confirmFeed.value = null
    await state.run()
  } catch (err) {
    toast.error(err)
  } finally {
    removing.value = false
  }
}
</script>

<template>
  <div>
    <PageHeader
      title="公众号管理"
      description="订阅列表来自数据源的管理接口；列表为空时先确认访问令牌是否配好。"
    >
      <template #actions>
        <AppButton variant="outline" size="sm" :loading="state.loading.value" @click="state.run()">
          <template #icon>
            <PhArrowClockwise :size="14" aria-hidden="true" />
          </template>
          刷新列表
        </AppButton>
      </template>
    </PageHeader>

    <section class="panel mb-5 rounded-panel px-5 py-4">
      <AppField
        label="按文章链接添加公众号"
        :hint="urlHint || '该能力取决于数据源实现（we-mp-rss 不支持时需在其管理界面里订阅）'"
      >
        <template #default="{ id }">
          <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
            <!-- AppInput 是 inheritAttrs:false 且会丢掉 class，宽度只能由外层容器给 -->
            <div class="min-w-0 sm:flex-1">
              <AppInput
                :id="id"
                v-model="articleUrl"
                type="url"
                mono
                clearable
                placeholder="https://mp.weixin.qq.com/s/..."
                @enter="add"
              />
            </div>
            <AppButton variant="solid" :loading="adding" @click="add">
              <template #icon>
                <PhPlus :size="14" aria-hidden="true" />
              </template>
              添加
            </AppButton>
          </div>
        </template>
      </AppField>
    </section>

    <!-- 刷新失败但手里还有旧数据：留着列表，只在上方提示 -->
    <AppErrorState
      v-if="state.error.value && state.data.value"
      class="mb-5"
      :message="state.error.value"
      hint="列表读不到通常是数据源不可达或访问令牌失效——可先去「数据源状态」看一眼。"
      @retry="state.run()"
    />

    <section class="panel overflow-hidden rounded-panel">
      <header class="flex items-center justify-between gap-4 border-b border-hairline px-5 py-3.5">
        <h2 class="m-0 text-[14.5px] font-semibold text-ink">订阅列表</h2>
        <span v-if="feeds.length" class="nums font-mono text-xs text-faint">{{ feeds.length }} 个</span>
      </header>

      <AppErrorState
        v-if="state.error.value && !state.data.value"
        class="m-5"
        :message="state.error.value"
        hint="列表读不到通常是数据源不可达或访问令牌失效——可先去「数据源状态」看一眼。"
        @retry="state.run()"
      />

      <div v-else-if="state.loading.value && !state.data.value" class="flex flex-col gap-3 p-5">
        <div v-for="index in 4" :key="index" class="flex items-center gap-4">
          <AppSkeleton variant="block" width="48px" height="48px" />
          <div class="flex min-w-0 flex-1 flex-col gap-2">
            <AppSkeleton variant="line" width="34%" />
            <AppSkeleton variant="line" width="68%" />
          </div>
        </div>
      </div>

      <ul v-else-if="feeds.length" class="m-0 list-none p-0">
        <li
          v-for="feed in feeds"
          :key="feed.id"
          class="row-hover flex items-start gap-4 border-t border-hairline px-4 py-3.5 first:border-t-0 hover:bg-raised/40 sm:px-5"
        >
          <div class="size-12 shrink-0 overflow-hidden rounded-[10px] border border-hairline bg-inset">
            <img
              v-if="showCover(feed)"
              :src="feed.cover"
              :alt="`${feed.name} 封面`"
              class="size-full object-cover"
              loading="lazy"
              referrerpolicy="no-referrer"
              @error="markCoverFailed(feed.id)"
            />
            <div
              v-else
              class="grid size-full place-items-center text-[16px] font-medium text-muted"
              :style="placeholderStyle(feed.name)"
              aria-hidden="true"
            >
              {{ initialOf(feed.name) }}
            </div>
          </div>

          <div class="min-w-0 flex-1">
            <p class="m-0 truncate text-[14px] font-medium text-ink">{{ feed.name }}</p>
            <!-- 数据源不提供简介时不占位：29 行「（未提供简介）」只是噪音 -->
            <p
              v-if="feed.intro"
              class="m-0 mt-0.5 line-clamp-2 text-[12.5px] leading-relaxed text-muted"
            >
              {{ feed.intro }}
            </p>
            <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-faint">
              <span class="inline-flex items-center gap-1.5">
                同步
                <AppTooltip :content="formatDateTime(feed.sync_time)">
                  <span class="nums cursor-default font-mono">{{ formatRelative(feed.sync_time) }}</span>
                </AppTooltip>
              </span>
              <span class="inline-flex items-center gap-1.5">
                更新
                <AppTooltip :content="formatDateTime(feed.update_time)">
                  <span class="nums cursor-default font-mono">{{ formatRelative(feed.update_time) }}</span>
                </AppTooltip>
              </span>
            </div>
          </div>

          <AppButton variant="outline" size="sm" @click="confirmFeed = feed">
            <template #icon>
              <PhTrash :size="14" aria-hidden="true" />
            </template>
            取消订阅
          </AppButton>
        </li>
      </ul>

      <AppEmptyState
        v-else-if="showEmpty && !tokenConfigured"
        :icon="PhKey"
        title="读不到订阅列表：数据源的订阅接口需要访问令牌"
        description="上游 /api/v1/wx/mps 需要 Bearer Token，未配置时只会返回空列表（当前未配置）。"
      >
        <template #hint>
          <p class="m-0 mt-2 max-w-[56ch] text-xs leading-relaxed text-faint">
            可在「配置管理 → 文章数据源 → 访问令牌」里填入并保存，或直接到
            <code>http://localhost:8001</code> 查看与管理订阅。
          </p>
        </template>
        <template #action>
          <AppButton variant="tonal" size="sm" @click="router.push('/settings')">
            去配置访问令牌
          </AppButton>
        </template>
      </AppEmptyState>

      <AppEmptyState
        v-else-if="showEmpty"
        :icon="PhRss"
        title="还没有订阅任何公众号"
        description="在上面的输入框里粘贴一篇该公众号的文章链接即可添加。"
      />

      <div v-else-if="state.loading.value" class="p-5">
        <AppSkeleton variant="rows" :rows="4" />
      </div>

      <!-- 兜底：刷新失败且手里没有可用的旧数据 -->
      <AppEmptyState
        v-else
        :icon="PhRss"
        title="订阅列表暂时读不到"
        description="先按上面的提示处理，再重试一次。"
        compact
      >
        <template #action>
          <AppButton variant="outline" size="sm" @click="state.run()">重试</AppButton>
        </template>
      </AppEmptyState>
    </section>

    <AppDialog
      :open="confirmFeed !== null"
      title="取消订阅"
      :description="confirmFeed ? `确定取消订阅《${confirmFeed.name}》？` : ''"
      width="sm"
      @update:open="confirmFeed = null"
    >
      <p class="m-0 text-[13px] leading-relaxed text-muted">
        只影响数据源里的订阅关系：已抓取并生成的文章笔记不会被删除。
      </p>
      <template #footer>
        <AppButton variant="ghost" :disabled="removing" @click="confirmFeed = null">返回</AppButton>
        <AppButton variant="danger" :loading="removing" @click="remove">取消订阅</AppButton>
      </template>
    </AppDialog>
  </div>
</template>
