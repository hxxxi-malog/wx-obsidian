<script setup lang="ts">
/**
 * 文章详情抽屉。
 *
 * 数据来源有两层，先到先画：
 *  - `article`：列表行数据（点击即渲染，体感零延迟）；
 *  - `detail`：详情接口的权威记录（重试/删除后状态更准）。
 * 两者字段同构，展示一律 `detail ?? article`，所以详情请求失败也还能看、能操作。
 *
 * 布局只用间距与发丝线分层：概要标签 → 元信息 dl → 文件路径 → 错误详情，不叠卡片。
 */
import { PhArrowSquareOut, PhArrowsClockwise, PhArticle, PhTrash } from '@phosphor-icons/vue'
import { computed } from 'vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppCopyButton from '@/components/ui/AppCopyButton.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppEmptyState from '@/components/ui/AppEmptyState.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppTag from '@/components/ui/AppTag.vue'
import type { ArticleItem } from '@/lib/api/types'
import { formatDateTime, formatRelative } from '@/lib/format'
import { articleStatusMeta } from '@/lib/status'

const props = withDefaults(
  defineProps<{
    open: boolean
    /** 列表行（立即渲染用） */
    article?: ArticleItem | null
    /** 详情接口返回的权威记录 */
    detail?: ArticleItem | null
    /** 详情请求进行中 */
    loading?: boolean
    /** 详情请求失败的原因（列表数据仍在，故只降级为一条错误条） */
    error?: string
    opening?: boolean
    retrying?: boolean
    deleting?: boolean
  }>(),
  {
    article: null,
    detail: null,
    loading: false,
    error: '',
    opening: false,
    retrying: false,
    deleting: false,
  },
)

const emit = defineEmits<{
  (event: 'update:open', value: boolean): void
  (event: 'open'): void
  (event: 'retry'): void
  (event: 'remove'): void
  (event: 'reload'): void
}>()

const view = computed<ArticleItem | null>(() => props.detail ?? props.article)
const status = computed(() => articleStatusMeta(view.value?.status ?? ''))
const busy = computed(() => props.opening || props.retrying || props.deleting)
</script>

<template>
  <AppDrawer
    :open="open"
    :title="view?.title || '文章详情'"
    width="lg"
    @update:open="emit('update:open', $event)"
  >
    <AppSkeleton v-if="!view && loading" variant="rows" :rows="6" />

    <AppEmptyState
      v-else-if="!view"
      :icon="PhArticle"
      compact
      title="没有读到这篇文章的记录"
      description="它可能已经被删除或清理，重新加载列表看看。"
    />

    <div v-else class="flex flex-col gap-5">
      <div class="flex flex-wrap items-center gap-2">
        <AppTag :tone="status.tone" variant="soft">{{ status.label }}</AppTag>
        <AppTag :tone="view.file_exists ? 'success' : 'danger'" variant="outline">
          {{ view.file_exists ? '文件在库' : '文件缺失' }}
        </AppTag>
        <span v-if="loading" class="text-xs text-faint">读取详情…</span>
      </div>

      <AppErrorState
        v-if="error"
        compact
        :message="error"
        hint="列表里的数据仍然可用；重新读取可获得最新状态。"
        retry-label="重新读取"
        @retry="emit('reload')"
      />

      <dl class="m-0 grid grid-cols-[72px_minmax(0,1fr)] gap-x-3 gap-y-3 text-[13px]">
        <dt class="m-0 text-faint">分类</dt>
        <dd class="m-0 break-words text-ink">{{ view.category || '—' }}</dd>

        <dt class="m-0 text-faint">子主题</dt>
        <dd class="m-0 break-words text-ink">{{ view.sub_topic || '—' }}</dd>

        <dt class="m-0 text-faint">状态</dt>
        <dd class="m-0 text-ink">{{ status.label }}</dd>

        <dt class="m-0 text-faint">处理时间</dt>
        <dd class="m-0 text-ink">
          <span class="font-mono nums">{{ formatDateTime(view.processed_at) }}</span>
          <span class="ml-2 text-xs text-faint">{{ formatRelative(view.processed_at) }}</span>
        </dd>

        <dt class="m-0 text-faint">重试次数</dt>
        <dd class="m-0 text-ink">
          <span class="font-mono nums">{{ view.retry_count }}</span>
          <span class="ml-1 text-xs text-faint">次</span>
        </dd>

        <dt class="m-0 text-faint">文章 ID</dt>
        <dd class="m-0 flex items-start gap-1">
          <span class="min-w-0 font-mono nums text-xs leading-relaxed break-all text-muted">
            {{ view.article_id }}
          </span>
          <AppCopyButton :value="view.article_id" label="复制 ID" size="sm" variant="ghost" />
        </dd>
      </dl>

      <section class="flex flex-col gap-1.5">
        <h3 class="m-0 text-[11.5px] font-medium tracking-wide text-faint">文件路径</h3>
        <div class="rounded-control border border-hairline bg-inset px-3 py-2">
          <p class="m-0 font-mono text-xs leading-relaxed break-all text-ink">
            {{ view.file_path || '—' }}
          </p>
          <div class="mt-1.5 flex justify-end">
            <AppCopyButton
              v-if="view.file_path"
              :value="view.file_path"
              label="复制路径"
              size="sm"
              variant="ghost"
            />
          </div>
        </div>
      </section>

      <section v-if="view.error" class="flex flex-col gap-1.5">
        <h3 class="m-0 text-[11.5px] font-medium tracking-wide text-faint">错误详情</h3>
        <div class="rounded-control border border-danger/30 bg-danger-soft px-3 py-2">
          <!-- 原文照搬：多行错误（含可执行指引）必须保留换行，不允许截断 -->
          <p class="m-0 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-ink">
            {{ view.error }}
          </p>
          <div class="mt-1.5 flex justify-end">
            <AppCopyButton :value="view.error" label="复制错误" size="sm" variant="ghost" />
          </div>
        </div>
      </section>
    </div>

    <template #footer>
      <template v-if="view">
        <!-- 主次分明：删除最弱（靠左、幽灵态）、重试次之（描边）、打开是唯一实心主操作 -->
        <AppButton size="sm" variant="ghost" :loading="deleting" :disabled="busy" @click="emit('remove')">
          <template #icon>
            <PhTrash :size="14" class="text-danger" aria-hidden="true" />
          </template>
          删除
        </AppButton>

        <AppButton
          size="sm"
          variant="outline"
          :loading="retrying"
          :disabled="busy"
          @click="emit('retry')"
        >
          <template #icon>
            <PhArrowsClockwise :size="14" aria-hidden="true" />
          </template>
          重试
        </AppButton>

        <AppButton
          class="ml-auto"
          size="sm"
          variant="solid"
          :loading="opening"
          :disabled="busy"
          @click="emit('open')"
        >
          <template #icon>
            <PhArrowSquareOut :size="14" aria-hidden="true" />
          </template>
          在 Obsidian 打开
        </AppButton>
      </template>
      <span v-else class="text-xs text-faint">没有可操作的文章</span>
    </template>
  </AppDrawer>
</template>
