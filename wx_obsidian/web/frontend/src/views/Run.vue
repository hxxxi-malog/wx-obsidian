<script setup lang="ts">
/**
 * 文章抓取：启动台 + 实时进度 + 本次结果 + 失败文章 + 运行日志 + 系统告警。
 *
 * 状态来源只有一个——`useJobsStore()`：SSE 归约、断线重连、终态关闭都在 store 里，
 * 页面**不自己建 EventSource**，也不轮询任务状态（挂载时 `refresh()` 复核一次即可）。
 * 系统告警走 `GET /api/logs/events` 的 SSE（服务端缓冲只收 WARNING 以上）：
 * 终端里看不到的告警，必须能在界面上看到——这是这一栏存在的唯一理由。
 */
import {
  PhArrowClockwise,
  PhCheckCircle,
  PhInfo,
  PhLightning,
  PhPlay,
  PhTerminalWindow,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, nextTick, onMounted, ref } from 'vue'

import FailureList from '@/components/domain/FailureList.vue'
import JobProgressPanel from '@/components/domain/JobProgressPanel.vue'
import LogConsole, { type LogConsoleLine } from '@/components/domain/LogConsole.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDataTable, { type DataTableColumn } from '@/components/ui/AppDataTable.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppEmptyState from '@/components/ui/AppEmptyState.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppNumberInput from '@/components/ui/AppNumberInput.vue'
import AppPanel from '@/components/ui/AppPanel.vue'
import AppSegmented from '@/components/ui/AppSegmented.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import AppTag from '@/components/ui/AppTag.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useLogStream } from '@/composables/useLogStream'
import { useToast } from '@/composables/useToast'
import { JOB_ITEM_STATUS, type JobResultItem } from '@/lib/api/types'
import { formatElapsed } from '@/lib/format'
import { jobStateMeta } from '@/lib/status'
import { useJobsStore } from '@/stores/jobs'
import { useUiStore } from '@/stores/ui'

const jobs = useJobsStore()
const ui = useUiStore()
const toast = useToast()

/** 与旧实现一致的档位：0 = 不限（预设里显示为「不限」） */
const LIMIT_PRESETS = [0, 5, 20, 50]

const limit = ref(0)
const force = ref(false)
const confirmForce = ref(false)
/** 首次复核（GET /api/jobs）是否还没回来——决定进度面板显示骨架还是空态 */
const refreshing = ref(true)
/** 提交中（本地即刻置位，不等 store 的 state 回推） */
const submitting = ref(false)
const retryingAll = ref(false)
const retryingOne = ref('')
const itemFilter = ref<'all' | 'failed'>('all')

const failureList = ref<InstanceType<typeof FailureList> | null>(null)
const progressAnchor = ref<HTMLElement | null>(null)

const alerts = useLogStream({ max: 200 })
const alertLevel = ref('ALL')
const alertKeyword = ref('')

const stateMeta = computed(() => jobStateMeta(jobs.job?.state))
const running = computed(() => jobs.running)
const submitBusy = computed(() => running.value || submitting.value)

const stageText = computed(() => jobs.stageLabel)

const elapsedLabel = computed(() =>
  jobs.job ? formatElapsed(jobs.job.started_at, jobs.job.finished_at) : '—',
)

// -- 本次结果 ---------------------------------------------------------------

const resultColumns: DataTableColumn[] = [
  { key: 'title', label: '标题' },
  { key: 'category', label: '分类', width: '120px', hideBelow: 'md' },
  { key: 'status', label: '状态', width: '84px' },
  { key: 'error', label: '原因', width: '220px', hideBelow: 'sm' },
]

const sortedItems = computed(() =>
  [...jobs.items].sort((left, right) => {
    const leftFailed = left.status === 'done' ? 1 : 0
    const rightFailed = right.status === 'done' ? 1 : 0
    return leftFailed - rightFailed
  }),
)

const visibleItems = computed(() =>
  itemFilter.value === 'failed'
    ? sortedItems.value.filter((item) => item.status !== 'done')
    : sortedItems.value,
)

const failedCount = computed(
  () => sortedItems.value.filter((item) => item.status !== 'done').length,
)

const itemFilterOptions = computed(() => [
  { label: '全部', value: 'all', count: jobs.items.length },
  { label: '只看失败', value: 'failed', count: failedCount.value },
])

const resultEmptyText = computed(() => {
  if (itemFilter.value === 'failed') {
    return '本次没有失败的文章'
  }
  if (!jobs.job) {
    return '还没有发起过抓取'
  }
  if (jobs.running) {
    return '任务正在跑，明细会在每篇处理完后出现'
  }
  return '本次没有需要处理的文章'
})

const resultEmptyHint = computed(() => {
  if (itemFilter.value === 'failed') {
    return '把筛选切回「全部」可以看到成功与跳过的条目。'
  }
  if (!jobs.job) {
    return '在上面的启动台里选好条数，点「开始抓取」即可。'
  }
  return '常见原因：数据源里没有新文章，或全部已在 processed.json 里（可打开「强制重新处理」）。'
})

function resultStatus(item: JobResultItem): {
  label: string
  tone: 'success' | 'danger' | 'warning' | 'info'
} {
  return JOB_ITEM_STATUS[item.status] ?? { label: item.status || '未知', tone: 'info' }
}

function itemTitle(item: JobResultItem): string {
  return item.title || '（无标题）'
}

// -- 运行日志 ---------------------------------------------------------------

const jobLogLines = computed<LogConsoleLine[]>(() =>
  jobs.logLines.map((line) => ({
    seq: line.seq,
    ts: line.ts,
    level: line.level,
    text: line.text,
  })),
)

// -- 系统告警（WARNING 以上） ------------------------------------------------

const ALERT_LEVELS = [
  { label: '全部', value: 'ALL' },
  { label: '警告', value: 'WARNING' },
  { label: '错误', value: 'ERROR' },
]

const alertLines = computed<LogConsoleLine[]>(() => {
  const keyword = alertKeyword.value.trim().toLowerCase()
  return alerts.entries.value
    .filter((entry) => {
      const level = (entry.level || '').toUpperCase()
      if (alertLevel.value === 'WARNING' && level !== 'WARNING') {
        return false
      }
      if (alertLevel.value === 'ERROR' && level !== 'ERROR' && level !== 'CRITICAL') {
        return false
      }
      if (!keyword) {
        return true
      }
      return (
        (entry.message || '').toLowerCase().includes(keyword) ||
        (entry.logger || '').toLowerCase().includes(keyword)
      )
    })
    .map((entry) => ({
      seq: entry.seq,
      ts: entry.ts,
      level: entry.level,
      logger: entry.logger,
      text: entry.message,
    }))
})

const alertEmptyText = computed(() =>
  alerts.entries.value.length
    ? '当前筛选下没有条目'
    : '暂无告警：数据源不可达、保活失败、定时抓取被跳过这类问题都会出现在这里',
)

// -- 动作 -------------------------------------------------------------------

/** 把正在跑的任务拉回页面：复核一次后展开任务坞，并把进度面板滚进视野。 */
async function loadCurrentJob(): Promise<void> {
  await jobs.refresh()
  await nextTick()
  ui.openDock()
  progressAnchor.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

/**
 * 手动点「刷新状态」：store 把读取失败收进 jobs.error（页内错误块会说明原因），
 * 这里只补成功反馈；任务首次复核仍直接调 jobs.refresh()，不弹提示。
 */
async function refreshStatus(): Promise<void> {
  const ok = await jobs.refresh()
  if (!ok) {
    return
  }
  toast.success('刷新成功', jobs.job ? '已同步当前任务状态' : '当前没有任务')
}

/**
 * 启动前的复核：后端单飞通道会以 409 job_busy 拒绝重复提交，
 * 而 store 的 `startFetch` 只回 false（错误在 `jobs.error` 里，且丢掉了错误码），
 * 所以先查一次 `/api/jobs`，把「已有任务在跑」这条最可能的原因翻译成带操作的提示。
 */
async function beginFetch(): Promise<void> {
  submitting.value = true
  try {
    await jobs.refresh()
    if (jobs.job?.state === 'running') {
      toast.warn(
        '已有任务在运行',
        `当前任务 ${jobs.job.completed}/${jobs.job.total || '?'} 正在进行，不能重复提交`,
        {
          label: '查看当前任务',
          run: () => void loadCurrentJob(),
        },
      )
      return
    }
    const ok = await jobs.startFetch({ limit: limit.value, force: force.value })
    if (ok) {
      toast.success(
        '已提交抓取任务',
        force.value
          ? `强制重新处理，最多 ${limit.value || '不限'} 篇`
          : `最多 ${limit.value || '不限'} 篇`,
      )
      return
    }
    const message = jobs.error || '提交失败'
    // 409 单飞拒绝（可能来自另一个标签页/定时任务）用更精确的说明，其余走通用兜底
    const busy = jobs.errorCode === 'job_busy'
    toast.error(message, busy ? '已有任务在运行，进度见任务坞' : '可打开任务坞查看当前任务状态', {
      label: '打开任务坞',
      run: () => void loadCurrentJob(),
    })
  } finally {
    submitting.value = false
  }
}

function onStartClick(): void {
  if (submitBusy.value) {
    return
  }
  // 强制重跑会重复消耗 API 额度，先过一次确认
  if (force.value) {
    confirmForce.value = true
    return
  }
  void beginFetch()
}

function confirmAndStart(): void {
  confirmForce.value = false
  void beginFetch()
}

async function cancelJob(): Promise<void> {
  await jobs.cancel()
}

/** 重试全部失败文章：没有失败文章时给 info 提示（旧文案语义保留） */
async function retryAll(): Promise<void> {
  retryingAll.value = true
  try {
    await jobs.refresh()
    const failed = jobs.failedItems
    if (!failed.length) {
      toast.info('没有失败文章需要重试')
      return
    }
    const submitted = await jobs.startFetch({ articleIds: failed.map((item) => item.article_id) })
    if (submitted) {
      toast.success(`已提交 ${failed.length} 篇重试`)
      return
    }
    toast.error(jobs.error || '提交重试失败', '可打开任务坞查看当前任务状态', {
      label: '打开任务坞',
      run: () => void loadCurrentJob(),
    })
  } finally {
    retryingAll.value = false
  }
}

/** 单篇重试：只提交这一篇（FailureList 负责列表渲染，动作留在页面里统一提示） */
async function retryOne(articleId: string): Promise<void> {
  retryingOne.value = articleId
  try {
    const submitted = await jobs.startFetch({ articleIds: [articleId] })
    if (!submitted) {
      toast.error(jobs.error || '提交重试失败', '可打开任务坞查看当前任务状态', {
        label: '打开任务坞',
        run: () => void loadCurrentJob(),
      })
      return
    }
    toast.success('已提交重试')
    // 重新读一遍失败列表：这一篇可能刚被后端从 failed.json 里摘掉
    await jobs.refresh()
    failureList.value?.reload()
  } finally {
    retryingOne.value = ''
  }
}

onMounted(() => {
  // 只在挂载时复核一次；任务进度由 store 的 SSE 推，不需要轮询
  void jobs.refresh().finally(() => {
    refreshing.value = false
  })
  alerts.start()
})
</script>

<template>
  <div class="flex min-w-0 flex-col gap-5">
    <PageHeader
      title="文章抓取"
      description="发起一轮抓取并实时跟踪进度。任务跑在服务进程里，浏览器关掉也照常执行；同一时间只允许一个任务。"
    >
      <template #meta>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <AppTag :tone="stateMeta.tone">{{ stateMeta.label }}</AppTag>
          <span v-if="jobs.job" class="nums font-mono text-[11.5px] text-faint">
            {{ jobs.job.completed }}/{{ jobs.job.total || '?' }} · 已用 {{ elapsedLabel }}
          </span>
          <span v-if="stageText" class="text-[11.5px] text-faint">当前阶段：{{ stageText }}</span>
        </div>
      </template>

      <template #actions>
        <AppButton
          size="sm"
          variant="outline"
          :loading="refreshing"
          :disabled="running"
          @click="refreshStatus"
        >
          <template #icon><PhArrowClockwise :size="14" aria-hidden="true" /></template>
          刷新状态
        </AppButton>
      </template>
    </PageHeader>

    <AppErrorState
      v-if="jobs.error"
      compact
      :message="jobs.error"
      hint="如果是「已有任务在运行」，先等当前任务结束或取消它；也可以打开右下角任务坞查看进度。"
      retry-label="重新读取任务状态"
      @retry="loadCurrentJob()"
    />

    <div class="grid min-w-0 grid-cols-1 gap-5 lg:grid-cols-3">
      <!-- 启动台 -->
      <AppPanel as="section" class="min-w-0">
        <h3 class="m-0 flex items-center gap-2 text-[13.5px] font-medium text-ink">
          <PhLightning :size="15" class="text-accent" aria-hidden="true" />
          启动台
        </h3>
        <p class="m-0 mt-1 text-[12px] leading-relaxed text-faint">
          选好处理条数与强制策略后提交；运行中不能再提交第二个任务。
        </p>

        <div class="mt-4 flex flex-col gap-1.5">
          <span class="text-[12.5px] font-medium text-muted">处理条数</span>
          <AppNumberInput
            v-model="limit"
            :min="0"
            :max="200"
            :presets="LIMIT_PRESETS"
            :disabled="submitBusy"
          />
          <span class="text-[11.5px] text-faint">
            0 = 不限，按数据源里的顺序一直处理到没有新文章为止。
          </span>
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-2.5 border-t border-hairline pt-4">
          <AppSwitch v-model="force" :disabled="submitBusy" label="强制重新处理已处理过的文章" />
          <span class="text-[13px] text-ink">强制重新处理已处理过的文章</span>
          <AppTooltip
            content="会绕过 processed.json 的判重，把处理过的文章再跑一遍：每次都会重新调用 DeepSeek API，重复消耗额度，生成的文件也会被覆盖。"
            side="top"
          >
            <button
              type="button"
              class="grid size-5 shrink-0 place-items-center rounded-full text-faint transition-colors hover:text-ink"
              aria-label="强制重新处理的说明"
            >
              <PhInfo :size="14" aria-hidden="true" />
            </button>
          </AppTooltip>
        </div>

        <p v-if="force" class="m-0 mt-2 text-[11.5px] leading-relaxed text-warning">
          已开启强制重跑：点「开始抓取」会先弹一次确认。
        </p>

        <div class="mt-5 flex flex-wrap items-center gap-2 border-t border-hairline pt-4">
          <AppButton
            variant="solid"
            :loading="submitBusy"
            :disabled="submitBusy"
            @click="onStartClick"
          >
            <template #icon><PhPlay :size="14" aria-hidden="true" /></template>
            开始抓取
          </AppButton>
          <AppButton variant="danger" :disabled="!running" @click="cancelJob">取消</AppButton>
        </div>

        <div class="mt-3 flex flex-wrap items-center gap-2">
          <AppButton
            size="sm"
            variant="outline"
            :disabled="submitBusy || retryingAll || !jobs.failedItems.length"
            :loading="retryingAll"
            @click="retryAll"
          >
            <template #icon><PhArrowClockwise :size="13" aria-hidden="true" /></template>
            重试失败文章
          </AppButton>
          <span class="text-[11.5px] text-faint">没有失败文章时按钮不可用；有则提交它们的快照。</span>
        </div>

        <p class="m-0 mt-4 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-faint">
          与 CLI 不能同时抓取：两边各自持有任务状态，会争抢 processed.json。
        </p>
      </AppPanel>

      <!-- 进度：没有任务时给空态，首次复核未回来给骨架 -->
      <div class="min-w-0 lg:col-span-2">
        <div v-if="jobs.job" ref="progressAnchor" class="min-w-0">
          <JobProgressPanel
            :job="jobs.job"
            :connected="jobs.connected"
            :elapsed-ms="jobs.elapsedMs"
            :percent="jobs.percent"
            :running="running"
            :loading="refreshing"
            @cancel="cancelJob"
          />
        </div>
        <AppPanel v-else-if="refreshing" as="section" class="min-w-0">
          <div class="flex flex-col gap-3">
            <div class="shimmer h-5 w-32 rounded-[6px]" />
            <div class="shimmer h-9 w-40 rounded-[8px]" />
            <div class="shimmer h-1.5 w-full rounded-full" />
            <div class="shimmer h-6 w-3/4 rounded-[8px]" />
          </div>
        </AppPanel>
        <AppPanel v-else as="section" class="min-w-0">
          <AppEmptyState
            :icon="PhTerminalWindow"
            title="当前没有任务"
            description="在左边的启动台里设好条数，点「开始抓取」；进度、阶段时间线与当前文章会实时出现在这里。"
          />
        </AppPanel>
      </div>

      <!-- 本次结果 -->
      <AppPanel v-if="jobs.items.length" as="section" class="min-w-0 lg:col-span-2">
        <div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 pb-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="m-0 text-[13.5px] font-medium text-ink">本次结果</h3>
              <AppTag tone="success">成功 {{ jobs.summary.done ?? 0 }}</AppTag>
              <AppTag :tone="failedCount ? 'danger' : 'neutral'">
                失败 {{ jobs.summary.failed ?? 0 }}
              </AppTag>
            </div>
            <p class="m-0 mt-1 text-[12px] leading-relaxed text-faint">
              这一轮里每一篇的处理结果；失败条目排在前面。
            </p>
          </div>
          <AppSegmented v-model="itemFilter" :options="itemFilterOptions" size="sm" />
        </div>

        <AppDataTable
          :columns="resultColumns"
          :rows="visibleItems"
          :row-key="(row: JobResultItem) => row.article_id"
          density="compact"
          :skeleton-rows="0"
        >
          <template #cell-title="{ row }">
            <span class="block max-w-[52ch] truncate" :title="row.title">{{ itemTitle(row) }}</span>
          </template>
          <template #cell-category="{ row }">
            <span v-if="row.category" class="text-muted">{{ row.category }}</span>
            <span v-else class="text-faint">—</span>
          </template>
          <template #cell-status="{ row }">
            <AppTag :tone="resultStatus(row).tone">{{ resultStatus(row).label }}</AppTag>
          </template>
          <template #cell-error="{ row }">
            <span
              v-if="row.error"
              class="block max-w-[34ch] truncate font-mono text-[11.5px] text-danger"
              :title="row.error"
            >
              {{ row.error }}
            </span>
            <span v-else class="text-faint">—</span>
          </template>
          <template #empty>
            <AppEmptyState
              compact
              :icon="itemFilter === 'failed' ? PhCheckCircle : PhInfo"
              :title="resultEmptyText"
              :description="resultEmptyHint"
            />
          </template>
        </AppDataTable>
      </AppPanel>

      <!-- 失败文章：加载失败降级为空列表，不把整页变成错误态 -->
      <AppPanel as="section" class="min-w-0">
        <FailureList
          ref="failureList"
          :retrying="submitBusy || Boolean(retryingOne)"
          @retry="retryOne"
          @retry-all="retryAll"
        />
      </AppPanel>
    </div>

    <!-- 运行日志（任务事件，SSE） -->
    <AppPanel as="section" class="min-w-0">
      <LogConsole
        :lines="jobLogLines"
        title="运行日志"
        description="本轮任务的事件流（开始、逐篇进度、阶段切换、失败原因）。用户往上滚动时自动暂停跟随，不会抢走你正在看的位置。"
        empty-text="任务开始后，事件会一行行出现在这里"
        :connected="jobs.connected"
        max-height="320px"
        @clear="jobs.clearLog()"
      />
    </AppPanel>

    <!-- 系统告警：终端里看不到的告警，必须能在界面上看到 -->
    <AppPanel as="section" class="min-w-0">
      <LogConsole
        :lines="alertLines"
        title="系统告警（WARNING 以上）"
        description="来自 GET /api/logs/events 的实时告警流：数据源不可达、保活失败、定时抓取被跳过这类问题，终端里看不到的，这里必须能看到。切到后台标签页会自动断开，回到页面自动续上。"
        :empty-text="alertEmptyText"
        :connected="alerts.connected.value"
        max-height="260px"
        @clear="alerts.clear()"
      >
        <template #actions>
          <AppSegmented v-model="alertLevel" :options="ALERT_LEVELS" size="sm" />
          <div class="w-[190px]">
            <AppInput
              v-model="alertKeyword"
              size="sm"
              type="search"
              clearable
              placeholder="按关键字过滤…"
            />
          </div>
        </template>
      </LogConsole>
    </AppPanel>

    <!-- 强制重跑的二次确认：避免误触重复烧 API 额度 -->
    <AppDialog
      v-model:open="confirmForce"
      title="确认强制重新处理？"
      description="这一步会绕过 processed.json 的判重"
      width="sm"
    >
      <p class="m-0 flex items-start gap-2 text-[13px] leading-relaxed text-ink">
        <PhWarningCircle :size="16" class="mt-px shrink-0 text-warning" aria-hidden="true" />
        <span>
          已处理过的文章会被<b>重新调用 DeepSeek API</b> 生成笔记，重复消耗额度；
          生成的文件会被覆盖。<template v-if="limit">本次最多处理 {{ limit }} 篇。</template>
          <template v-else>本次不限篇数。</template>
        </span>
      </p>
      <template #footer>
        <AppButton variant="ghost" @click="confirmForce = false">取消</AppButton>
        <AppButton variant="solid" @click="confirmAndStart">仍然开始</AppButton>
      </template>
    </AppDialog>
  </div>
</template>
