<script setup lang="ts">
/**
 * 定时任务：定时抓取 + 保活，两个任务的设定与状态。
 *
 * 这一页保留了两条**运维事实**的说明文案（不是装饰）：
 *  1. 任务跑在服务进程里：浏览器关掉也照常执行；设定写进配置，重启后自动恢复。
 *  2. 定时抓取与手动抓取共用单飞通道：已有任务在跑时本次会被跳过并记一条警告，
 *     不会去争抢 processed.json。
 *
 * 倒计时用一个每秒自增的 ref 驱动（页面不可见时停掉，避免空转）；
 * 任务列表本身不需要轮询——它只在动作之后变化。
 */
import {
  PhArrowClockwise,
  PhClock,
  PhHeartbeat,
  PhInfo,
  PhPlay,
  PhStop,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useDocumentVisibility } from '@vueuse/core'

import AppButton from '@/components/ui/AppButton.vue'
import AppEmptyState from '@/components/ui/AppEmptyState.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppPanel from '@/components/ui/AppPanel.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import AppTag from '@/components/ui/AppTag.vue'
import AppTooltip from '@/components/ui/AppTooltip.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useToast } from '@/composables/useToast'
import { apiDelete, apiGet, apiPost } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { SchedulerJob, SchedulerPayload, SchedulerState } from '@/lib/api/types'
import { formatCountdown, formatDateTime, formatRelative } from '@/lib/format'

const FETCH_JOB_ID = 'fetch_job'
const KEEPALIVE_JOB_ID = 'keepalive_job'

const toast = useToast()
const visibility = useDocumentVisibility()

const jobs = ref<SchedulerJob[]>([])
const state = ref<SchedulerState | null>(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')

/** 本地秒表：只服务倒计时（等宽数字每秒递减）。页面隐藏时停掉，不做无意义的空转。 */
const now = ref(new Date())
let ticker: number | null = null

const fetchJob = computed(() => jobs.value.find((job) => job.job_id === FETCH_JOB_ID) ?? null)
const keepaliveJob = computed(
  () => jobs.value.find((job) => job.job_id === KEEPALIVE_JOB_ID) ?? null,
)

const fetchHours = ref(0)
const keepaliveEnabled = ref(false)

const intervalOptions = computed(() =>
  (state.value?.allowed_hours ?? [1, 2, 6, 12, 24]).map((hours) => ({
    label: `每 ${hours} 小时`,
    value: String(hours),
  })),
)

const intervalValue = computed({
  get: () => String(fetchHours.value),
  set: (value: string) => {
    fetchHours.value = Number(value) || 0
  },
})

// -- 秒表 -------------------------------------------------------------------

function startTicker(): void {
  if (ticker !== null) {
    return
  }
  now.value = new Date()
  ticker = window.setInterval(() => {
    now.value = new Date()
  }, 1000)
}

function stopTicker(): void {
  if (ticker !== null) {
    window.clearInterval(ticker)
    ticker = null
  }
}

watch(visibility, (value) => {
  if (value === 'hidden') {
    stopTicker()
  } else {
    now.value = new Date()
    startTicker()
  }
})

onBeforeUnmount(stopTicker)

// -- 取数与动作 -------------------------------------------------------------

function apply(payload: SchedulerPayload): void {
  jobs.value = payload.jobs ?? []
  state.value = payload.state ?? null
  fetchHours.value = payload.state?.fetch_hours ?? 0
  keepaliveEnabled.value = payload.state?.keepalive_enabled ?? false
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    apply(await apiGet<SchedulerPayload>(endpoints.scheduler.jobs()))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function saveFetch(): Promise<void> {
  if (fetchHours.value <= 0) {
    toast.warn('请先选择抓取周期', '周期为必选项；只想手动跑就用下面的「立即执行一次」')
    return
  }
  busy.value = true
  try {
    const payload = await apiPost<SchedulerPayload & { ok: boolean }>(endpoints.scheduler.set(), {
      kind: 'fetch',
      interval: `${fetchHours.value}h`,
    })
    apply(payload)
    toast.success(
      fetchJob.value ? '已更新定时抓取' : '已启动定时抓取',
      `每 ${fetchHours.value} 小时`,
    )
  } catch (err) {
    toast.error(err, '设置定时抓取失败')
  } finally {
    busy.value = false
  }
}

async function stopFetch(): Promise<void> {
  busy.value = true
  try {
    apply(await apiDelete<SchedulerPayload>(endpoints.scheduler.remove(FETCH_JOB_ID)))
    toast.success('已停止定时抓取')
  } catch (err) {
    toast.error(err, '停止定时抓取失败')
  } finally {
    busy.value = false
  }
}

async function toggleKeepalive(value: boolean): Promise<void> {
  busy.value = true
  try {
    apply(
      await apiPost<SchedulerPayload>(endpoints.scheduler.set(), {
        kind: 'keepalive',
        enabled: value,
      }),
    )
    toast.success(value ? '已启动保活任务' : '已停止保活任务')
  } catch (err) {
    toast.error(err, '设置保活任务失败')
    // 提交失败：把开关拨回服务端的真实状态，不留假象
    await load()
  } finally {
    busy.value = false
  }
}

/** 立即执行一次。抓取任务走单飞通道，已有任务在跑时后端返回 409。 */
async function runNow(jobId: string): Promise<void> {
  try {
    await apiPost(endpoints.scheduler.runNow(jobId))
    toast.success(
      jobId === FETCH_JOB_ID ? '已触发，进度见「文章抓取」页' : '已触发保活',
      jobId === FETCH_JOB_ID ? '任务跑在服务进程里，可以离开这一页' : undefined,
    )
    await load()
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    if (jobId === FETCH_JOB_ID && /job_busy|已有任务/.test(message)) {
      toast.warn(
        '已有任务在运行',
        '定时抓取与手动抓取共用单飞通道，本次没有提交；等当前任务结束后再试。',
      )
    } else {
      toast.error(err, '触发失败')
    }
  }
}

function jobName(jobId: string): string {
  return jobId === FETCH_JOB_ID ? '定时抓取' : jobId === KEEPALIVE_JOB_ID ? '保活' : jobId
}

onMounted(() => {
  void load()
  startTicker()
})
</script>

<template>
  <div class="flex min-w-0 flex-col gap-5">
    <PageHeader
      title="定时任务"
      description="任务跑在服务进程里：浏览器关掉也照常执行；设定会写进配置，重启后自动恢复。"
    >
      <template #meta>
        <div class="mt-2 flex flex-wrap items-center gap-2 text-[11.5px] text-faint">
          <span class="inline-flex items-center gap-1.5">
            <AppStatusDot :tone="fetchJob ? 'success' : 'neutral'" :pulse="Boolean(fetchJob)" />
            定时抓取 {{ fetchJob ? `每 ${state?.fetch_hours ?? 0} 小时` : '未启用' }}
          </span>
          <span class="inline-flex items-center gap-1.5">
            <AppStatusDot :tone="keepaliveEnabled ? 'success' : 'neutral'" />
            保活 {{ keepaliveEnabled ? '已启用' : '未启用' }}
          </span>
        </div>
      </template>

      <template #actions>
        <AppButton size="sm" variant="outline" :loading="loading" :disabled="busy" @click="load">
          <template #icon><PhArrowClockwise :size="14" aria-hidden="true" /></template>
          刷新
        </AppButton>
      </template>
    </PageHeader>

    <AppErrorState
      v-if="error"
      :message="error"
      hint="定时任务由服务进程里的调度器持有；读不到列表时先确认应用仍在运行。"
      @retry="load"
    />

    <div class="grid min-w-0 grid-cols-1 gap-5 lg:grid-cols-2">
      <!-- 定时抓取 -->
      <AppPanel as="section" class="min-w-0">
        <header class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div class="min-w-0">
            <h3 class="m-0 flex items-center gap-2 text-[13.5px] font-medium text-ink">
              <PhClock :size="15" class="text-accent" aria-hidden="true" />
              定时抓取
            </h3>
            <p class="m-0 mt-1 text-[12px] leading-relaxed text-faint">
              按固定间隔自动发起一轮抓取，与手动抓取走同一条管线。
            </p>
          </div>
          <AppTag :tone="fetchJob ? 'success' : 'neutral'">
            {{ fetchJob ? '运行中' : '未启用' }}
          </AppTag>
        </header>

        <div class="mt-4 flex flex-col gap-1.5">
          <span class="text-[12.5px] font-medium text-muted">抓取周期</span>
          <div class="w-full sm:max-w-[200px]">
            <AppSelect v-model="intervalValue" :options="intervalOptions" :disabled="busy" />
          </div>
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-2 border-t border-hairline pt-4">
          <AppButton variant="solid" :disabled="busy" :loading="busy" @click="saveFetch">
            <template #icon><PhPlay :size="14" aria-hidden="true" /></template>
            {{ fetchJob ? '更新周期' : '启动定时抓取' }}
          </AppButton>
          <AppButton variant="danger" :disabled="busy || !fetchJob" @click="stopFetch">
            <template #icon><PhStop :size="14" aria-hidden="true" /></template>
            停止
          </AppButton>
          <AppButton variant="outline" :disabled="busy || !fetchJob" @click="runNow(FETCH_JOB_ID)">
            立即执行一次
          </AppButton>
        </div>

        <!-- 本次/下次触发 -->
        <dl
          class="m-0 mt-4 grid grid-cols-1 gap-x-4 gap-y-2 border-t border-hairline pt-4 sm:grid-cols-2"
        >
          <div class="flex min-w-0 flex-col gap-0.5">
            <dt class="text-[11.5px] text-faint">下次触发</dt>
            <dd class="m-0 flex flex-wrap items-baseline gap-2">
              <span class="nums font-mono text-[13px] text-ink">
                {{ fetchJob ? formatCountdown(fetchJob.next_run, now) : '—' }}
              </span>
              <span v-if="fetchJob?.next_run" class="nums font-mono text-[11px] text-faint">
                {{ formatDateTime(fetchJob.next_run) }}
              </span>
            </dd>
          </div>
          <div class="flex min-w-0 flex-col gap-0.5">
            <dt class="text-[11.5px] text-faint">最近一次</dt>
            <dd class="m-0 flex flex-wrap items-baseline gap-2">
              <span class="text-[13px] text-ink">
                {{ fetchJob ? formatRelative(fetchJob.last_run, now) : '—' }}
              </span>
              <span v-if="fetchJob?.last_run" class="nums font-mono text-[11px] text-faint">
                {{ formatDateTime(fetchJob.last_run) }}
              </span>
            </dd>
          </div>
        </dl>

        <p
          class="m-0 mt-4 flex items-start gap-2 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-muted"
        >
          <PhInfo :size="14" class="mt-px shrink-0 text-faint" aria-hidden="true" />
          <span>
            定时抓取会走与手动抓取相同的单飞通道：如果已有任务在跑，本次会被跳过并记一条警告，
            不会争抢 processed.json。
          </span>
        </p>
      </AppPanel>

      <!-- 保活 -->
      <AppPanel as="section" class="min-w-0">
        <header class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div class="min-w-0">
            <h3 class="m-0 flex items-center gap-2 text-[13.5px] font-medium text-ink">
              <PhHeartbeat :size="15" class="text-accent" aria-hidden="true" />
              保活任务
            </h3>
            <p class="m-0 mt-1 text-[12px] leading-relaxed text-faint">
              每 {{ state?.keepalive_days ?? 7 }} 天刷新微信读书
              Cookie，避免登录态过期导致抓取失败。
            </p>
          </div>
          <AppTag :tone="keepaliveEnabled ? 'success' : 'neutral'">
            {{ keepaliveEnabled ? '已启用' : '已停用' }}
          </AppTag>
        </header>

        <div class="mt-4 flex flex-wrap items-center gap-2.5">
          <AppSwitch
            :model-value="keepaliveEnabled"
            :disabled="busy"
            label="启用保活任务"
            @update:model-value="toggleKeepalive"
          />
          <span class="text-[13px] text-ink">
            每 {{ state?.keepalive_days ?? 7 }} 天刷新微信读书 Cookie
          </span>
          <AppTooltip
            content="关闭后不再自动刷新 Cookie；登录态过期时抓取会失败，需要到「账号管理」重新扫码。"
            side="top"
          >
            <button
              type="button"
              class="grid size-5 shrink-0 place-items-center rounded-full text-faint transition-colors hover:text-ink"
              aria-label="保活任务的说明"
            >
              <PhInfo :size="14" aria-hidden="true" />
            </button>
          </AppTooltip>
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-2 border-t border-hairline pt-4">
          <AppButton
            variant="outline"
            :disabled="busy || !keepaliveJob"
            @click="runNow(KEEPALIVE_JOB_ID)"
          >
            立即执行一次
          </AppButton>
          <span class="text-[11.5px] text-faint">立即执行不受开关限制，但需要任务已存在。</span>
        </div>

        <dl
          class="m-0 mt-4 grid grid-cols-1 gap-x-4 gap-y-2 border-t border-hairline pt-4 sm:grid-cols-2"
        >
          <div class="flex min-w-0 flex-col gap-0.5">
            <dt class="text-[11.5px] text-faint">下次触发</dt>
            <dd class="m-0 flex flex-wrap items-baseline gap-2">
              <span class="nums font-mono text-[13px] text-ink">
                {{ keepaliveJob ? formatCountdown(keepaliveJob.next_run, now) : '—' }}
              </span>
              <span v-if="keepaliveJob?.next_run" class="nums font-mono text-[11px] text-faint">
                {{ formatDateTime(keepaliveJob.next_run) }}
              </span>
            </dd>
          </div>
          <div class="flex min-w-0 flex-col gap-0.5">
            <dt class="text-[11.5px] text-faint">最近一次</dt>
            <dd class="m-0 flex flex-wrap items-baseline gap-2">
              <span class="text-[13px] text-ink">
                {{ keepaliveJob ? formatRelative(keepaliveJob.last_run, now) : '—' }}
              </span>
              <span v-if="keepaliveJob?.last_run" class="nums font-mono text-[11px] text-faint">
                {{ formatDateTime(keepaliveJob.last_run) }}
              </span>
            </dd>
          </div>
        </dl>
      </AppPanel>
    </div>

    <!-- 任务状态表 -->
    <AppPanel as="section" class="min-w-0">
      <header class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 pb-3">
        <div class="min-w-0">
          <h3 class="m-0 text-[13.5px] font-medium text-ink">任务状态</h3>
          <p class="m-0 mt-1 text-[12px] leading-relaxed text-faint">
            调度器当前持有的任务；「触发规则」是 APScheduler 的 interval 表达式。
          </p>
        </div>
        <AppTag v-if="jobs.length" tone="neutral" mono>{{ jobs.length }}</AppTag>
      </header>

      <AppSkeleton v-if="loading" variant="rows" :rows="3" />

      <AppEmptyState
        v-else-if="!jobs.length"
        compact
        :icon="PhClock"
        title="当前没有定时任务"
        description="在上面选一个抓取周期并「启动定时抓取」，或打开保活开关；设定会写进配置，重启后自动恢复。"
      />

      <ul v-else class="m-0 flex list-none flex-col p-0">
        <li
          v-for="job in jobs"
          :key="job.job_id"
          class="flex flex-wrap items-start gap-x-4 gap-y-2 border-b border-hairline py-3 last:border-b-0"
        >
          <div class="flex min-w-[160px] flex-col gap-0.5">
            <span class="flex items-center gap-2">
              <AppStatusDot :tone="job.is_running ? 'accent' : 'neutral'" :pulse="job.is_running" />
              <span class="text-[13px] font-medium text-ink">{{ jobName(job.job_id) }}</span>
            </span>
            <span class="nums pl-4 font-mono text-[11px] text-faint">{{ job.job_id }}</span>
          </div>

          <div class="flex min-w-[150px] flex-1 flex-col gap-0.5">
            <span class="text-[11.5px] text-faint">触发规则</span>
            <span class="nums font-mono text-[12.5px] text-muted">{{ job.trigger || '—' }}</span>
          </div>

          <div class="flex min-w-[170px] flex-col gap-0.5">
            <span class="text-[11.5px] text-faint">下次触发</span>
            <span class="flex flex-wrap items-baseline gap-2">
              <span class="nums font-mono text-[12.5px] text-ink">
                {{ formatCountdown(job.next_run, now) }}
              </span>
              <span v-if="job.next_run" class="nums font-mono text-[11px] text-faint">
                {{ formatDateTime(job.next_run) }}
              </span>
            </span>
          </div>

          <div class="flex min-w-[170px] flex-col gap-0.5">
            <span class="text-[11.5px] text-faint">最近一次</span>
            <span class="flex flex-wrap items-baseline gap-2">
              <span class="text-[12.5px] text-ink">{{ formatRelative(job.last_run, now) }}</span>
              <span v-if="job.last_run" class="nums font-mono text-[11px] text-faint">
                {{ formatDateTime(job.last_run) }}
              </span>
            </span>
          </div>

          <div class="flex min-w-[110px] flex-col gap-0.5">
            <span class="text-[11.5px] text-faint">错误次数</span>
            <span class="flex items-center gap-2">
              <AppTag v-if="job.error_count > 0" tone="danger" mono>
                {{ job.error_count }} 次
              </AppTag>
              <span v-else class="text-[12.5px] text-faint">—</span>
            </span>
          </div>

          <p
            v-if="job.last_error"
            class="m-0 flex w-full items-start gap-2 rounded-control border border-danger/25 bg-danger-soft px-3 py-2 text-[11.5px] leading-relaxed text-ink"
          >
            <PhWarningCircle :size="14" class="mt-px shrink-0 text-danger" aria-hidden="true" />
            <span class="min-w-0 break-words">{{ job.last_error }}</span>
          </p>
        </li>
      </ul>
    </AppPanel>
  </div>
</template>
