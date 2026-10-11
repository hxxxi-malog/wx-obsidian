<script setup lang="ts">
/**
 * 命令面板（⌘K / Ctrl+K）：
 *  页面跳转 + 全局操作 + 文章搜索（走 GET /api/articles?q=）。
 * 键盘优先：↑↓ 选择、↵ 执行、Esc 关闭；鼠标 hover 会同步高亮项。
 */
import { PhArticle, PhArrowRight, PhMagnifyingGlass } from '@phosphor-icons/vue'
import { DialogContent, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import AppKbd from '@/components/ui/AppKbd.vue'
import { apiGet, isAbort } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { ArticleList } from '@/lib/api/types'
import { ALL_NAV_ITEMS } from '@/lib/navigation'
import { useJobsStore } from '@/stores/jobs'
import { useToast } from '@/composables/useToast'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (event: 'update:open', value: boolean): void }>()

interface Command {
  id: string
  group: string
  label: string
  hint?: string
  run: () => void
}

const ui = useUiStore()
const jobs = useJobsStore()
const router = useRouter()
const toast = useToast()

const query = ref('')
const activeIndex = ref(0)
const articles = ref<{ id: string; title: string }[]>([])
const searching = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)
let searchTimer: number | null = null
let searchController: AbortController | null = null

const commands = computed<Command[]>(() => {
  const list: Command[] = ALL_NAV_ITEMS.map((item) => ({
    id: `nav-${item.key}`,
    group: '跳转',
    label: item.label,
    hint: item.description,
    run: () => void router.push(item.path),
  }))

  list.push(
    {
      id: 'job-start',
      group: '操作',
      label: '发起抓取（不限篇数）',
      hint: jobs.running ? '已有任务在运行，会被拒绝' : '与「文章抓取」页的开始按钮等价',
      run: () => {
        void jobs.startFetch({ limit: 0 }).then((ok) => {
          if (ok) {
            toast.success('已提交抓取任务')
          } else {
            toast.error(jobs.error || '提交失败')
          }
        })
      },
    },
    {
      id: 'theme',
      group: '操作',
      label: '切换主题',
      hint: `当前：${ui.themeMode === 'system' ? '跟随系统' : ui.themeMode === 'dark' ? '深色' : '浅色'}`,
      run: () => {
        const next = ui.cycleTheme()
        toast.info(`主题已切换为${next === 'system' ? '跟随系统' : next === 'dark' ? '深色' : '浅色'}`)
      },
    },
    {
      id: 'run-page',
      group: '操作',
      label: '打开抓取进度与日志',
      run: () => void router.push('/run'),
    },
  )

  if (jobs.running) {
    list.push({
      id: 'job-cancel',
      group: '操作',
      label: '取消当前任务',
      hint: '会请求管线在安全点停止',
      run: () => void jobs.cancel(),
    })
  }

  return list
})

const filtered = computed(() => {
  const term = query.value.trim().toLowerCase()
  if (!term) {
    return commands.value
  }
  return commands.value.filter(
    (item) => item.label.toLowerCase().includes(term) || (item.hint ?? '').toLowerCase().includes(term),
  )
})

interface Row {
  key: string
  group: string
  label: string
  hint?: string
  run: () => void
  isArticle?: boolean
}

const rows = computed<Row[]>(() => {
  const list: Row[] = filtered.value.map((item) => ({ ...item, key: item.id }))
  articles.value.forEach((article) => {
    list.push({
      key: `article-${article.id}`,
      group: '文章',
      label: article.title,
      hint: '在文章管理中打开',
      isArticle: true,
      run: () => void router.push({ path: '/articles', query: { q: article.title } }),
    })
  })
  return list
})

const grouped = computed(() => {
  const map = new Map<string, { row: Row; index: number }[]>()
  rows.value.forEach((row, index) => {
    const bucket = map.get(row.group) ?? []
    bucket.push({ row, index })
    map.set(row.group, bucket)
  })
  return Array.from(map.entries()).map(([group, items]) => ({ group, items }))
})

async function searchArticles(term: string): Promise<void> {
  searchController?.abort()
  if (term.trim().length < 2) {
    articles.value = []
    return
  }
  const controller = new AbortController()
  searchController = controller
  searching.value = true
  try {
    const payload = await apiGet<ArticleList>(
      endpoints.articles.list({ q: term.trim(), limit: 6 }),
      controller.signal,
    )
    articles.value = payload.items.map((item) => ({ id: item.article_id, title: item.title }))
  } catch (err) {
    if (!isAbort(err)) {
      articles.value = []
    }
  } finally {
    searching.value = false
  }
}

watch(query, (term) => {
  activeIndex.value = 0
  if (searchTimer !== null) {
    window.clearTimeout(searchTimer)
  }
  searchTimer = window.setTimeout(() => void searchArticles(term), 250)
})

watch(
  () => props.open,
  (open) => {
    if (open) {
      query.value = ''
      articles.value = []
      activeIndex.value = 0
      void nextTick(() => inputRef.value?.focus())
    } else {
      searchController?.abort()
    }
  },
)

function close(): void {
  emit('update:open', false)
}

function runRow(row: Row | undefined): void {
  if (!row) {
    return
  }
  close()
  row.run()
}

function move(delta: number): void {
  const total = rows.value.length
  if (total === 0) {
    return
  }
  activeIndex.value = (activeIndex.value + delta + total) % total
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    move(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    move(-1)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    runRow(rows.value[activeIndex.value])
  }
}
</script>

<template>
  <DialogRoot :open="props.open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-40 bg-overlay" />
      <DialogContent
        class="panel fixed top-[12vh] left-1/2 z-50 w-[min(620px,calc(100vw-32px))] -translate-x-1/2 overflow-hidden rounded-panel shadow-float"
        @keydown="onKeydown"
      >
        <DialogTitle class="sr-only">命令面板</DialogTitle>

        <div class="flex items-center gap-2.5 border-b border-hairline px-4 py-3">
          <PhMagnifyingGlass :size="16" class="shrink-0 text-faint" aria-hidden="true" />
          <input
            ref="inputRef"
            v-model="query"
            type="text"
            class="min-w-0 flex-1 bg-transparent text-[14px] text-ink outline-none placeholder:text-faint"
            placeholder="跳转页面、执行操作、搜索文章标题…"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
          />
          <AppKbd keys="Esc" />
        </div>

        <div id="palette-list" class="max-h-[52dvh] overflow-y-auto p-1.5" role="listbox">
          <template v-for="bucket in grouped" :key="bucket.group">
            <p class="m-0 px-2.5 pt-2 pb-1 text-[11px] font-medium text-faint">{{ bucket.group }}</p>
            <button
              v-for="entry in bucket.items"
              :key="entry.row.key"
              type="button"
              role="option"
              :aria-selected="entry.index === activeIndex"
              class="flex w-full items-center gap-2.5 rounded-[9px] px-2.5 py-2 text-left transition-colors"
              :class="entry.index === activeIndex ? 'bg-raised' : 'hover:bg-raised/70'"
              @mouseenter="activeIndex = entry.index"
              @click="runRow(entry.row)"
            >
              <PhArticle v-if="entry.row.isArticle" :size="15" class="shrink-0 text-faint" aria-hidden="true" />
              <PhArrowRight v-else :size="14" class="shrink-0 text-faint" aria-hidden="true" />
              <span class="min-w-0 flex-1 truncate text-[13px] text-ink">{{ entry.row.label }}</span>
              <span v-if="entry.row.hint" class="hidden max-w-[46%] truncate text-[11.5px] text-faint sm:block">
                {{ entry.row.hint }}
              </span>
            </button>
          </template>

          <p v-if="rows.length === 0" class="m-0 px-3 py-6 text-center text-[13px] text-faint">
            {{ searching ? '正在搜索文章…' : '没有匹配的页面、操作或文章' }}
          </p>
        </div>

        <footer class="flex items-center gap-3 border-t border-hairline bg-inset px-4 py-2 text-[11px] text-faint">
          <span class="flex items-center gap-1"><AppKbd keys="↑" /><AppKbd keys="↓" /> 选择</span>
          <span class="flex items-center gap-1"><AppKbd keys="↵" /> 执行</span>
          <span class="ml-auto">输入 2 个字以上可搜文章</span>
        </footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
