<script setup lang="ts">
/**
 * 目录选择器（浏览模式）。
 *
 * 为什么必须走后端：浏览器沙箱拿不到本地绝对路径（webkitdirectory 只给相对路径），
 * 所以目录浏览只能由 `endpoints.fs.list()` 提供。
 *
 * 对外契约保持不变：`v-model` 控制开关、`startPath` 给起点、`picked(path)` 回传结果。
 */
import { PhArrowUp, PhFolder, PhFolderOpen } from '@phosphor-icons/vue'
import { computed, nextTick, ref, watch } from 'vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppCopyButton from '@/components/ui/AppCopyButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppEmptyState from '@/components/ui/AppEmptyState.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import { apiGet, errorMessage } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { FsListing } from '@/lib/api/types'

const props = defineProps<{ modelValue: boolean; startPath: string }>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'picked', path: string): void
}>()

const listing = ref<FsListing | null>(null)
const loading = ref(false)
const error = ref('')
const activeIndex = ref(-1)

const listRef = ref<HTMLElement | null>(null)
const rowRefs = ref<HTMLElement[]>([])

/** 只列目录：文件对「选 Vault」没有意义，混进来只会让人多点几次 */
const dirs = computed(() => (listing.value?.entries ?? []).filter((entry) => entry.is_dir))
const current = computed(() => listing.value?.path ?? '')
const parent = computed(() => listing.value?.parent ?? '')

/** 起点为空时退回根目录：后端要求绝对路径，空路径只会得到 400 */
const fallbackPath = computed(() => props.startPath?.trim() || '/')

function childPath(name: string): string {
  const base = current.value
  if (!base || base === '/') {
    return `/${name}`
  }
  return `${base.replace(/\/+$/, '')}/${name}`
}

function setRowRef(element: unknown, index: number): void {
  if (element instanceof HTMLElement) {
    rowRefs.value[index] = element
  }
}

async function browse(path: string): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await apiGet<FsListing>(endpoints.fs.list(path))
    listing.value = data
    activeIndex.value = data.entries.some((entry) => entry.is_dir) ? 0 : -1
    await nextTick()
    listRef.value?.focus()
  } catch (err) {
    // 保留上一次的列表：进错目录（无权限等）时还能「上一级」退回去
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function move(delta: number): void {
  if (dirs.value.length === 0) {
    return
  }
  const total = dirs.value.length
  activeIndex.value = (activeIndex.value + delta + total) % total
  void nextTick(() => rowRefs.value[activeIndex.value]?.scrollIntoView({ block: 'nearest' }))
}

function enterActive(): void {
  const entry = dirs.value[activeIndex.value]
  if (!entry) {
    choose()
    return
  }
  void browse(childPath(entry.name))
}

function choose(): void {
  if (!current.value) {
    return
  }
  emit('picked', current.value)
  emit('update:modelValue', false)
}

function close(): void {
  emit('update:modelValue', false)
}

function onListKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    move(1)
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    move(-1)
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    enterActive()
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) {
      return
    }
    listing.value = null
    activeIndex.value = -1
    void browse(fallbackPath.value)
  },
)
</script>

<template>
  <AppDialog
    :open="modelValue"
    title="选择知识库目录"
    description="目录由本地服务读取——浏览器拿不到本地绝对路径，所以浏览必须走后端。"
    width="lg"
    @update:open="emit('update:modelValue', $event)"
  >
    <div class="flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <AppButton size="sm" variant="outline" :disabled="!parent || loading" @click="browse(parent)">
          <template #icon>
            <PhArrowUp :size="14" aria-hidden="true" />
          </template>
          上一级
        </AppButton>

        <p class="m-0 min-w-0 flex-1 truncate font-mono text-[12.5px] text-muted" :title="current">
          {{ current || '—' }}
        </p>
        <AppCopyButton v-if="current" :value="current" label="复制路径" />
      </div>

      <AppErrorState
        v-if="error"
        :message="error"
        hint="可以直接「上一级」退回去，或重试当前目录。"
        @retry="browse(current || fallbackPath)"
      />

      <AppSkeleton v-if="loading && !listing" variant="rows" :rows="6" />

      <div
        v-else
        ref="listRef"
        class="max-h-[320px] overflow-y-auto rounded-panel border border-hairline bg-inset p-1"
        role="listbox"
        tabindex="0"
        aria-label="子目录列表"
        @keydown="onListKeydown"
      >
        <AppSkeleton v-if="loading" variant="rows" :rows="5" />

        <template v-else-if="dirs.length">
          <button
            v-for="(entry, index) in dirs"
            :key="entry.name"
            :ref="(element) => setRowRef(element, index)"
            type="button"
            role="option"
            :aria-selected="index === activeIndex"
            class="flex w-full items-center gap-2.5 rounded-[8px] px-3 py-2 text-left text-[13px] text-ink transition-colors"
            :class="index === activeIndex ? 'bg-raised' : 'hover:bg-raised/60'"
            @mouseenter="activeIndex = index"
            @click="activeIndex = index"
            @dblclick="browse(childPath(entry.name))"
          >
            <PhFolder :size="15" class="shrink-0 text-faint" aria-hidden="true" />
            <span class="truncate">{{ entry.name }}</span>
          </button>
        </template>

        <AppEmptyState
          v-else
          :icon="PhFolderOpen"
          title="没有子目录"
          description="可以直接选择当前目录作为 Vault。"
          compact
        />
      </div>

      <p v-if="listing?.truncated" class="m-0 text-xs leading-relaxed text-warning">
        目录项过多，只列出了前 500 项；可以先用「上一级」缩小范围。
      </p>

      <p class="m-0 text-xs leading-relaxed text-faint">
        单击选中，双击或按 ↵ 进入子目录；↑↓ 切换选中项。
      </p>
    </div>

    <template #footer>
      <AppButton variant="ghost" @click="close">取消</AppButton>
      <AppButton variant="solid" :disabled="!current" @click="choose">选择当前目录</AppButton>
    </template>
  </AppDialog>
</template>
