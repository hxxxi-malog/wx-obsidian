<script setup lang="ts">
/**
 * 状态总览 `/`（V1）
 *
 * 保留的旧行为（逐条对应）：
 *  1. `endpoints.health()` + `endpoints.stats()` 首屏**并发**拉取；右上「刷新」手动重拉；页面可见时 30s 轮询；
 *  2. `first_run` 引导（原文案要点 + 「去配置」→ `/settings`）；
 *  3. `warnings[]` 逐条展示 message + action，没有告警时整块不出现；
 *  4. 四项组件状态（数据源 / LLM / Vision / Vault，null = 未配置）+ `overall` 总状态；
 *  5. 指标条：已处理 / 失败 / 合计 / 分类数 + 上次抓取（相对时间，无值「暂无记录」）；
 *  6. 分类分布：计数降序，点击某条 → `/articles?category=<name>`；
 *  7. 右栏最近任务（`jobs.recent`）+ 快捷操作（开始抓取 / 重试失败 / 打开配置）；
 *  8. loading / error / empty 三态齐全，错误可就地重试。
 *
 * 刻意改掉的两处呈现：
 *  - 旧版是「统计卡片行 + 分类列表塞在卡片里」，这里指标条无盒子（divide-x 发丝线），
 *    页面改不对称双栏（1.7fr / 1fr），盒子只用于真正的分组面板；
 *  - 旧版点分类不会跳转（只读列表），现在整行可点且键盘可达。
 *
 * 取数只在**页面**发生：`useAsync` 一次并发拿两个 DTO，避免两套 loading/error 各自闪烁。
 */
import {
  PhArrowClockwise,
  PhArrowCounterClockwise,
  PhGear,
  PhPlay,
  PhRocketLaunch,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import CategoryBars from '@/components/domain/CategoryBars.vue'
import HealthRow from '@/components/domain/HealthRow.vue'
import MetricStrip, { type MetricItem } from '@/components/domain/MetricStrip.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppPanel from '@/components/ui/AppPanel.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import AppTag from '@/components/ui/AppTag.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useAsync } from '@/composables/useAsync'
import { usePolling } from '@/composables/usePolling'
import { useToast } from '@/composables/useToast'
import { apiGet } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type {
  ConnectionTestResult,
  FailedArticle,
  HealthPayload,
  JobSnapshot,
  StatsPayload,
} from '@/lib/api/types'
import { formatDateTime, formatNumber, formatRelative, formatTime } from '@/lib/format'
import { jobStateMeta, type Tone } from '@/lib/status'
import { useJobsStore } from '@/stores/jobs'

/** 自动刷新间隔：与 spec 的「仪表盘 30s」一致；页面隐藏时 usePolling 自动暂停 */
const REFRESH_INTERVAL_MS = 30_000
/** 右栏只列最近几条：再多就变成第二个任务页了 */
const RECENT_JOB_LIMIT = 5
const FIRST_RUN_STEPS = ['设置 API Key', '选择 Vault 路径', '回「文章抓取」跑一轮']

const JOB_KIND_LABEL: Record<string, string> = {
  fetch: '抓取',
  retry: '重试',
}

interface Overview {
  health: HealthPayload
  stats: StatsPayload
}

interface ComponentRow {
  key: string
  label: string
  /** null = 未配置（后端返回 null，不是探测失败） */
  result: ConnectionTestResult | null
}

const router = useRouter()
const jobs = useJobsStore()
const toast = useToast()

/** 最近一次成功探测的时间（展示在组件状态栏右上，便于判断数据新不新） */
const checkedAt = ref('')
/** 任务列表是否至少读过一次：用于区分「还没读到」与「确实没有最近任务」 */
const jobsLoaded = ref(false)
const startingFetch = ref(false)
const retryingFailed = ref(false)

const {
  data: overview,
  loading,
  error,
  loaded,
  run: loadOverview,
} = useAsync<Overview>(
  async (signal) => {
    const [health, stats] = await Promise.all([
      apiGet<HealthPayload>(endpoints.health(), signal),
      apiGet<StatsPayload>(endpoints.stats(), signal),
    ])
    checkedAt.value = new Date().toISOString()
    return { health, stats }
  },
  { immediate: true },
)

const health = computed(() => overview.value?.health ?? null)
const stats = computed(() => overview.value?.stats ?? null)
/** 首屏骨架只在「第一次加载」出现：手动刷新与轮询不闪 */
const firstLoad = computed(() => loading.value && !loaded.value)
const hasData = computed(() => overview.value !== null)

const warnings = computed(() => health.value?.warnings ?? [])
const firstRun = computed(() => health.value?.first_run === true)
const overall = computed<{ label: string; tone: Tone } | null>(() => {
  const payload = health.value
  if (!payload) {
    return null
  }
  return payload.overall
    ? { label: '全部正常', tone: 'success' }
    : { label: '存在异常', tone: 'danger' }
})

const componentRows = computed<ComponentRow[]>(() => {
  const payload = health.value
  return [
    { key: 'source', label: '数据源 (we-mp-rss)', result: payload?.source ?? null },
    { key: 'llm', label: 'LLM API', result: payload?.llm_api ?? null },
    { key: 'vision', label: 'Vision API', result: payload?.vision_api ?? null },
    { key: 'vault', label: 'Vault 路径', result: payload?.vault_path ?? null },
  ]
})

/** 延迟微条的参照：同组内最大延迟（不跨组比较，避免「Vault 0ms 把 API 压成一条线」） */
const latencyMax = computed(() =>
  Math.max(0, ...componentRows.value.map((row) => row.result?.latency_ms ?? 0)),
)

const metrics = computed<MetricItem[]>(() => {
  const payload = stats.value
  const failed = payload?.failed_articles ?? 0
  return [
    {
      key: 'processed',
      label: '已处理',
      value: payload ? formatNumber(payload.processed_articles) : '—',
      hint: 'processed.json 里 status=done 的文章数',
    },
    {
      key: 'failed',
      label: '失败',
      value: payload ? formatNumber(failed) : '—',
      tone: failed > 0 ? 'danger' : 'neutral',
      hint: 'status=error 的文章数，可在「文章管理」里筛选后重试',
    },
    {
      key: 'total',
      label: '合计',
      value: payload ? formatNumber(payload.total_articles) : '—',
      hint: '已处理 + 失败（不含跳过与取消）',
    },
    {
      key: 'categories',
      label: '分类数',
      value: payload ? formatNumber(Object.keys(payload.categories).length) : '—',
      hint: '已处理文章覆盖到的分类数量',
    },
  ]
})

const lastFetch = computed(() => {
  const raw = stats.value?.last_fetch_time
  if (!raw) {
    return { text: '暂无记录', exact: '' }
  }
  return { text: formatRelative(raw), exact: formatDateTime(raw) }
})

const categoryItems = computed(() =>
  Object.entries(stats.value?.categories ?? {}).map(([name, count]) => ({ name, count })),
)

const failedCount = computed(() => stats.value?.failed_articles ?? 0)
const jobBusy = computed(() => jobs.running)
const recentJobs = computed(() => jobs.recent.slice(0, RECENT_JOB_LIMIT))

const actionHint = computed(() => {
  if (jobBusy.value) {
    return '已有任务在运行，进度见右下角任务坞。'
  }
  if (failedCount.value === 0) {
    return '当前没有失败文章。抓取会处理全部未处理过的文章。'
  }
  return `有 ${formatNumber(failedCount.value)} 篇失败文章可重试；抓取会处理全部未处理过的文章。`
})

function jobKindLabel(kind: string): string {
  return JOB_KIND_LABEL[kind] || kind || '任务'
}

/** 完成数：终态优先用 result.done（后端权威），运行中退回落 completed */
function jobProgress(job: JobSnapshot): string {
  const done = job.result?.done ?? job.completed
  const total = job.total || job.result?.total || 0
  return total > 0 ? `${done}/${total}` : String(done)
}

async function loadJobs(): Promise<void> {
  await jobs.refresh()
  jobsLoaded.value = true
}

/** 手动刷新 / 轮询共用的入口：健康、统计、最近任务一起更新，避免两块数据时间不一致 */
async function load(): Promise<void> {
  await Promise.all([loadOverview(), loadJobs()])
}

/**
 * 点「刷新」按钮才走这里：轮询仍走 load()，否则每 30 秒弹一次提示。
 * 检查结果里有异常组件时用警告语气，绿色提示只代表「这次读取成功」。
 */
async function manualRefresh(): Promise<void> {
  await load()
  if (error.value || !overview.value) {
    return
  }
  const checkedLabel = formatTime(checkedAt.value)
  if (overall.value?.tone === 'success') {
    toast.success('刷新成功', `${overall.value.label}，检查于 ${checkedLabel}`)
    return
  }
  toast.warn('刷新完成，但存在异常组件', `检查于 ${checkedLabel}，详见页面上的组件状态与告警`)
}

const polling = usePolling(load, REFRESH_INTERVAL_MS)

onMounted(() => {
  void loadJobs()
  polling.start()
})

async function startFetch(): Promise<void> {
  if (startingFetch.value || jobBusy.value) {
    return
  }
  startingFetch.value = true
  try {
    const ok = await jobs.startFetch({ limit: 0 })
    if (ok) {
      toast.success('已开始抓取', '进度会显示在右下角的任务坞里')
      await loadJobs()
      return
    }
    toast.error(jobs.error || '无法发起抓取', '开始抓取失败', {
      label: '去文章抓取',
      run: () => void router.push('/run'),
    })
  } finally {
    startingFetch.value = false
  }
}

/** 重试失败文章：先读失败列表拿 id，再以 kind=retry 提交（与旧文章页同一套语义） */
async function retryFailed(): Promise<void> {
  if (retryingFailed.value || jobBusy.value) {
    return
  }
  retryingFailed.value = true
  try {
    const items = await apiGet<FailedArticle[]>(endpoints.failed())
    const ids = (Array.isArray(items) ? items : [])
      .map((item) => item.article_id)
      .filter(Boolean)
    if (ids.length === 0) {
      toast.info('没有需要重试的文章', '失败列表是空的')
      await loadJobs()
      return
    }
    const ok = await jobs.startFetch({ articleIds: ids })
    if (ok) {
      toast.success(`已发起重试 ${ids.length} 篇`, '进度会显示在右下角的任务坞里')
      await loadJobs()
      return
    }
    toast.error(jobs.error || '无法发起重试', '重试失败', {
      label: '去文章抓取',
      run: () => void router.push('/run'),
    })
  } catch (err) {
    toast.error(err, '读取失败列表失败')
  } finally {
    retryingFailed.value = false
  }
}

function openCategory(name: string): void {
  void router.push({ path: '/articles', query: { category: name } })
}

function openSettings(): void {
  void router.push('/settings')
}
</script>

<template>
  <div class="min-w-0">
    <PageHeader
      title="状态总览"
      description="四项组件的连通性、处理统计与分类分布；页面可见时每 30 秒自动刷新。"
    >
      <template #meta>
        <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <AppTag v-if="overall" :tone="overall.tone" variant="soft">{{ overall.label }}</AppTag>
          <span v-if="checkedAt" class="font-mono nums text-[11.5px] text-faint">
            检查于 {{ formatTime(checkedAt) }}
          </span>
        </div>
      </template>

      <template #actions>
        <AppButton variant="outline" :loading="loading" @click="manualRefresh">
          <template #icon><PhArrowClockwise :size="14" aria-hidden="true" /></template>
          刷新
        </AppButton>
      </template>
    </PageHeader>

    <AppErrorState
      v-if="error"
      class="mb-5"
      :message="error"
      hint="点「重试」重新拉取；确认应用（或容器）仍在运行。"
      @retry="manualRefresh"
    />

    <!-- 首启引导：三步 + 去配置（原文案要点保留） -->
    <section
      v-if="firstRun"
      class="rise mb-5 flex flex-col gap-3 rounded-panel border border-warning/30 bg-warning-soft px-4 py-3.5 sm:flex-row sm:items-start sm:justify-between sm:gap-6"
    >
      <div class="min-w-0">
        <p class="m-0 flex items-center gap-2 text-[13.5px] font-medium text-ink">
          <PhRocketLaunch :size="16" class="shrink-0 text-warning" aria-hidden="true" />
          首次运行：还没有配置文件
        </p>
        <p class="m-0 mt-1.5 max-w-[68ch] text-[12.5px] leading-relaxed text-muted">
          先在「配置管理」里设置 Vault 路径与 API Key，再回「文章抓取」跑一轮。
        </p>
        <ol class="m-0 mt-3 flex list-none flex-wrap gap-x-5 gap-y-1.5 p-0 text-[12px] text-muted">
          <li v-for="(step, index) in FIRST_RUN_STEPS" :key="step" class="flex items-center gap-1.5">
            <span class="font-mono nums text-faint">{{ String(index + 1).padStart(2, '0') }}</span>
            <span>{{ step }}</span>
          </li>
        </ol>
      </div>
      <AppButton variant="solid" size="sm" class="shrink-0" @click="openSettings">
        <template #icon><PhGear :size="14" aria-hidden="true" /></template>
        去配置
      </AppButton>
    </section>

    <!-- 环境告警：逐条给「怎么修」，没有告警时整块不出现 -->
    <AppPanel v-if="warnings.length" padding="none" class="mb-5">
      <header
        class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-hairline px-4 py-3"
      >
        <h2 class="m-0 flex items-center gap-2 text-[13.5px] font-medium text-ink">
          <PhWarningCircle :size="16" class="shrink-0 text-warning" aria-hidden="true" />
          环境提示
        </h2>
        <span class="text-[11.5px] text-faint">这些不修好，抓取会失败</span>
      </header>
      <ul class="m-0 flex list-none flex-col p-0">
        <li
          v-for="warning in warnings"
          :key="warning.code"
          class="flex items-start gap-3 border-b border-hairline px-4 py-3 last:border-b-0"
        >
          <AppTag tone="warning" variant="outline" mono class="mt-0.5 shrink-0">
            {{ warning.code }}
          </AppTag>
          <div class="min-w-0">
            <p class="m-0 text-[13px] leading-relaxed text-ink">{{ warning.message }}</p>
            <p class="m-0 mt-1 text-[12px] leading-relaxed text-muted">{{ warning.action }}</p>
          </div>
        </li>
      </ul>
    </AppPanel>

    <template v-if="hasData || firstLoad">
      <!-- 指标条：无盒子，发丝线分隔 -->
      <section class="border-b border-hairline pb-5">
        <MetricStrip :items="metrics" :loading="firstLoad">
          <template #meta>
            <span>上次抓取</span>
            <AppTooltip :content="lastFetch.exact" :disabled="!lastFetch.exact">
              <span class="font-mono nums text-muted">{{ lastFetch.text }}</span>
            </AppTooltip>
          </template>
        </MetricStrip>
      </section>

      <!-- 不对称双栏：左宽（组件状态 / 分类分布）· 右窄（最近任务 / 快捷操作） -->
      <div class="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-[1.7fr_1fr]">
        <div class="flex min-w-0 flex-col gap-5">
          <AppPanel padding="none">
            <header
              class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-hairline px-4 py-3"
            >
              <h2 class="m-0 text-[13.5px] font-medium text-ink">组件状态</h2>
              <span class="text-[11.5px] text-faint">四项前置条件，缺一项就抓不成</span>
            </header>
            <div class="px-4 py-1">
              <HealthRow
                v-for="row in componentRows"
                :key="row.key"
                :label="row.label"
                :result="row.result"
                :loading="firstLoad"
                :latency-max="latencyMax"
              />
            </div>
          </AppPanel>

          <AppPanel padding="none">
            <header
              class="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 border-b border-hairline px-4 py-3"
            >
              <div class="min-w-0">
                <h2 class="m-0 text-[13.5px] font-medium text-ink">分类分布</h2>
                <p class="m-0 mt-0.5 text-[11.5px] text-faint">
                  计数降序 · 点任意一条跳到「文章管理」并筛该分类
                </p>
              </div>
              <AppTag v-if="categoryItems.length" mono>{{ categoryItems.length }}</AppTag>
            </header>
            <div class="px-3 py-3">
              <AppSkeleton v-if="firstLoad" variant="rows" :rows="6" />
              <CategoryBars v-else :items="categoryItems" @select="openCategory">
                <template #empty-action>
                  <AppButton
                    size="sm"
                    variant="outline"
                    :loading="startingFetch"
                    :disabled="jobBusy"
                    @click="startFetch"
                  >
                    开始抓取
                  </AppButton>
                </template>
              </CategoryBars>
            </div>
          </AppPanel>
        </div>

        <div class="flex min-w-0 flex-col gap-5">
          <AppPanel padding="none">
            <header
              class="flex items-center justify-between gap-x-3 border-b border-hairline px-4 py-3"
            >
              <h2 class="m-0 text-[13.5px] font-medium text-ink">最近任务</h2>
              <AppTag v-if="recentJobs.length" mono>{{ recentJobs.length }}</AppTag>
            </header>

            <div v-if="!jobsLoaded" class="px-4 py-4">
              <AppSkeleton variant="rows" :rows="3" />
            </div>

            <p
              v-else-if="recentJobs.length === 0"
              class="m-0 px-4 py-4 text-[12.5px] leading-relaxed text-faint"
            >
              本次启动还没有跑过任务。点下面「开始抓取」跑一轮，完成后会按时间列在这里。
            </p>

            <ul v-else class="m-0 flex list-none flex-col p-0">
              <li
                v-for="job in recentJobs"
                :key="job.job_id"
                class="flex items-center gap-3 border-b border-hairline px-4 py-2.5 last:border-b-0"
              >
                <AppStatusDot
                  :tone="jobStateMeta(job.state).tone"
                  :pulse="job.state === 'running'"
                  :size="8"
                />
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span class="text-[12.5px] text-ink">{{ jobKindLabel(job.kind) }}</span>
                    <AppTag :tone="jobStateMeta(job.state).tone" variant="soft">
                      {{ jobStateMeta(job.state).label }}
                    </AppTag>
                    <span
                      v-if="(job.result?.failed ?? 0) > 0"
                      class="font-mono nums text-[11px] text-danger"
                    >
                      失败 {{ job.result?.failed }}
                    </span>
                  </div>
                  <p v-if="job.last_title" class="m-0 mt-1 truncate text-[11.5px] text-faint">
                    {{ job.last_title }}
                  </p>
                </div>
                <div class="shrink-0 text-right">
                  <p class="m-0 font-mono nums text-[12.5px] text-ink">{{ jobProgress(job) }}</p>
                  <p class="m-0 mt-0.5 font-mono nums text-[10.5px] text-faint">
                    {{ formatRelative(job.finished_at || job.started_at) }}
                  </p>
                </div>
              </li>
            </ul>
          </AppPanel>

          <section class="min-w-0">
            <h2 class="m-0 text-[13.5px] font-medium text-ink">快捷操作</h2>
            <div class="mt-3 flex flex-col gap-2">
              <AppButton
                variant="solid"
                block
                :loading="startingFetch"
                :disabled="jobBusy"
                @click="startFetch"
              >
                <template #icon><PhPlay :size="14" aria-hidden="true" /></template>
                开始抓取（不限条数）
              </AppButton>
              <AppButton
                variant="outline"
                block
                :loading="retryingFailed"
                :disabled="jobBusy || failedCount === 0"
                @click="retryFailed"
              >
                <template #icon>
                  <PhArrowCounterClockwise :size="14" aria-hidden="true" />
                </template>
                重试失败文章
              </AppButton>
              <AppButton variant="ghost" block @click="openSettings">
                <template #icon><PhGear :size="14" aria-hidden="true" /></template>
                打开配置
              </AppButton>
            </div>
            <p class="m-0 mt-2.5 text-[11.5px] leading-relaxed text-faint">{{ actionHint }}</p>
          </section>
        </div>
      </div>
    </template>
  </div>
</template>
