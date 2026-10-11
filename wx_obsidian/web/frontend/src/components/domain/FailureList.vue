<script setup lang="ts">
/**
 * 失败文章列表（`GET /api/failed`）。
 *
 * 两条刻意保留的旧行为：
 *  1. **加载失败不整页报错**：这一栏是页面上的一个分区，接口挂了就降级成空列表 +
 *     一行可重试的提示，不能把整页变成错误态（旧实现就是 `catch { failures = [] }`）。
 *  2. **单篇重试**：每次都只提交 `{article_ids: [id]}`，不夹带列表里其它条目。
 *
 * 数据请求放在组件内：它只服务这一栏，提到页面里只会让 Run.vue 更难读。
 */
import { PhArrowClockwise, PhCheckCircle, PhWarningCircle } from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppEmptyState from '@/components/ui/AppEmptyState.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppTag from '@/components/ui/AppTag.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import { apiGet, errorMessage } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { FailedArticle } from '@/lib/api/types'
import { formatRelative, truncate } from '@/lib/format'

const props = withDefaults(
  defineProps<{
    /** 重试请求进行中（父级持有状态，按钮统一禁用，避免连点提交多个任务） */
    retrying?: boolean
  }>(),
  { retrying: false },
)

const emit = defineEmits<{
  (event: 'retry', articleId: string): void
  (event: 'retry-all'): void
}>()

const items = ref<FailedArticle[]>([])
const loading = ref(true)
/** 加载失败的原因（不阻断页面，只在本栏内提示 + 可重试） */
const loadError = ref('')

const hasItems = computed(() => items.value.length > 0)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const payload = await apiGet<FailedArticle[]>(endpoints.failed())
    items.value = Array.isArray(payload) ? payload : []
  } catch (err) {
    // 旧行为：接口失败就降级为空列表，页面其余部分照常可用
    items.value = []
    loadError.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)

defineExpose({ reload: load })
</script>

<template>
  <div class="flex min-w-0 flex-col">
    <header class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 pb-3">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="m-0 text-[13.5px] font-medium text-ink">失败文章</h3>
          <AppTag v-if="hasItems" tone="danger" mono>{{ items.length }}</AppTag>
        </div>
        <p class="m-0 mt-1 max-w-[80ch] text-[12px] leading-relaxed text-faint">
          这些文章在处理时出错，记录保留在本地；重试只针对选中的条目，不会重新抓取已成功的文章。
        </p>
      </div>

      <div class="flex flex-wrap items-center gap-1.5">
        <AppButton v-if="loadError" size="sm" variant="ghost" @click="load">
          <template #icon><PhArrowClockwise :size="13" aria-hidden="true" /></template>
          重新读取
        </AppButton>
        <AppButton
          v-if="hasItems"
          size="sm"
          variant="outline"
          :disabled="props.retrying"
          @click="emit('retry-all')"
        >
          <template #icon><PhArrowClockwise :size="13" aria-hidden="true" /></template>
          全部重试
        </AppButton>
      </div>
    </header>

    <p
      v-if="loadError"
      class="m-0 mb-3 flex items-start gap-2 rounded-control border border-warning/35 bg-warning-soft px-3 py-2 text-[12px] leading-relaxed text-ink"
    >
      <PhWarningCircle :size="15" class="mt-px shrink-0 text-warning" aria-hidden="true" />
      <span class="min-w-0">
        读取失败文章失败：{{ loadError }}
        <span class="text-muted">（不影响发起抓取）</span>
      </span>
    </p>

    <AppSkeleton v-if="loading" variant="rows" :rows="4" />

    <AppEmptyState
      v-else-if="!hasItems"
      compact
      :icon="PhCheckCircle"
      title="没有失败文章"
      description="当前 failed.json 里没有记录；处理出错的文章会出现在这里，可以单篇重试。"
    />

    <ul v-else class="m-0 flex list-none flex-col p-0">
      <li
        v-for="item in items"
        :key="item.article_id"
        class="flex flex-wrap items-start gap-x-3 gap-y-2 border-b border-hairline py-2.5 last:border-b-0"
      >
        <div class="min-w-0 flex-1">
          <p class="m-0 text-[13px] leading-snug text-ink" :title="item.title">
            {{ truncate(item.title || item.article_id, 120) }}
          </p>
          <p
            v-if="item.error"
            class="m-0 mt-1 font-mono text-[11.5px] leading-relaxed break-words text-danger"
          >
            {{ item.error }}
          </p>
          <p class="m-0 mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-faint">
            <span class="font-mono">{{ item.article_id }}</span>
            <span v-if="item.failed_at">· 失败于 {{ formatRelative(item.failed_at) }}</span>
          </p>
        </div>

        <div class="flex shrink-0 flex-wrap items-center gap-2">
          <AppTooltip :content="`这篇文章已经重试过 ${item.retry_count} 次`" side="left">
            <AppTag :tone="item.retry_count > 0 ? 'warning' : 'neutral'" mono>
              重试 {{ item.retry_count }} 次
            </AppTag>
          </AppTooltip>
          <AppButton
            size="sm"
            variant="tonal"
            :disabled="props.retrying"
            @click="emit('retry', item.article_id)"
          >
            重试
          </AppButton>
        </div>
      </li>
    </ul>
  </div>
</template>
