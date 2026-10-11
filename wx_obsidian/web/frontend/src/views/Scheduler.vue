<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { apiDelete, apiGet, apiPost, errorMessage } from '../api/client'
import type { SchedulerJob, SchedulerPayload } from '../api/types'

const jobs = ref<SchedulerJob[]>([])
const fetchHours = ref(0)
const keepaliveEnabled = ref(false)
const allowedHours = ref<number[]>([1, 2, 6, 12, 24])
const keepaliveDays = ref(7)
const loading = ref(false)
const busy = ref(false)
const error = ref('')

const fetchJob = computed(() => jobs.value.find((job) => job.job_id === 'fetch_job'))
const keepaliveJob = computed(() => jobs.value.find((job) => job.job_id === 'keepalive_job'))

function apply(payload: SchedulerPayload): void {
  jobs.value = payload.jobs
  fetchHours.value = payload.state.fetch_hours
  keepaliveEnabled.value = payload.state.keepalive_enabled
  allowedHours.value = payload.state.allowed_hours
  keepaliveDays.value = payload.state.keepalive_days
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    apply(await apiGet<SchedulerPayload>('/api/scheduler/jobs'))
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function startFetch(): Promise<void> {
  busy.value = true
  try {
    const payload = await apiPost<SchedulerPayload & { ok: boolean }>('/api/scheduler/jobs', {
      kind: 'fetch',
      interval: `${fetchHours.value}h`,
    })
    apply(payload)
    ElMessage.success(`已启动定时抓取（每 ${fetchHours.value} 小时）`)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    busy.value = false
  }
}

async function stopFetch(): Promise<void> {
  busy.value = true
  try {
    apply(await apiDelete<SchedulerPayload>('/api/scheduler/jobs/fetch_job'))
    ElMessage.success('已停止定时抓取')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    busy.value = false
  }
}

async function toggleKeepalive(value: boolean): Promise<void> {
  busy.value = true
  try {
    apply(
      await apiPost<SchedulerPayload>('/api/scheduler/jobs', {
        kind: 'keepalive',
        enabled: value,
      }),
    )
    ElMessage.success(value ? '已启动保活任务' : '已停止保活任务')
  } catch (err) {
    ElMessage.error(errorMessage(err))
    await load()
  } finally {
    busy.value = false
  }
}

async function runNow(jobId: string): Promise<void> {
  try {
    await apiPost(`/api/scheduler/jobs/${jobId}/run-now`)
    ElMessage.success('已触发，进度见「文章抓取」页')
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

function formatTime(raw: string | null): string {
  if (!raw) return '—'
  const parsed = new Date(raw)
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString('zh-CN')
}

onMounted(load)
</script>

<template>
  <div class="scheduler">
    <div class="scheduler__head">
      <h2 class="scheduler__title">定时任务</h2>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <el-alert
      title="任务跑在服务进程里：浏览器关掉也照常执行；设定会写进配置，重启后自动恢复"
      type="info"
      :closable="false"
      class="mb"
    />

    <el-card shadow="never" class="mb">
      <template #header><span>定时抓取</span></template>

      <div class="row">
        <span class="row__label">抓取周期</span>
        <el-select v-model="fetchHours" :disabled="busy" class="w-140">
          <el-option
            v-for="hours in allowedHours"
            :key="hours"
            :label="`每 ${hours} 小时`"
            :value="hours"
          />
        </el-select>
        <el-button type="primary" :disabled="busy" @click="startFetch">
          {{ fetchJob ? '更新周期' : '启动定时抓取' }}
        </el-button>
        <el-button type="danger" :disabled="busy || !fetchJob" @click="stopFetch">停止</el-button>
        <el-button :disabled="busy || !fetchJob" @click="runNow('fetch_job')">立即执行一次</el-button>
      </div>

      <div class="hint">
        定时抓取会走与手动抓取相同的单飞通道：如果已有任务在跑，本次会被跳过并记一条警告，
        不会争抢 processed.json。
      </div>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header><span>保活任务</span></template>
      <div class="row">
        <span class="row__label">每 {{ keepaliveDays }} 天刷新微信读书 Cookie</span>
        <el-switch
          :model-value="keepaliveEnabled"
          :disabled="busy"
          active-text="启用"
          inactive-text="停用"
          @update:model-value="toggleKeepalive"
        />
        <el-button :disabled="busy || !keepaliveJob" @click="runNow('keepalive_job')">立即执行一次</el-button>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header><span>任务状态</span></template>
      <el-table v-loading="loading" :data="jobs" size="small">
        <el-table-column prop="job_id" label="任务" width="140" />
        <el-table-column prop="trigger" label="触发规则" min-width="200" show-overflow-tooltip />
        <el-table-column label="下次触发" width="180">
          <template #default="{ row }">{{ formatTime(row.next_run) }}</template>
        </el-table-column>
        <el-table-column label="错误" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.error_count" type="danger" size="small">{{ row.error_count }} 次</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !jobs.length" description="当前没有定时任务" :image-size="60" />
    </el-card>
  </div>
</template>

<style scoped>
.scheduler__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.scheduler__title {
  margin: 0;
  font-size: 18px;
}

.mb {
  margin-bottom: 16px;
}

.row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.row__label {
  font-size: 14px;
}

.w-140 {
  width: 140px;
}

.hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--wx-muted);
}

.muted {
  color: var(--wx-muted);
}
</style>
