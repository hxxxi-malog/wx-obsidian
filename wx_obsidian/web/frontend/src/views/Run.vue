<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { apiGet, apiPost, errorMessage } from '../api/client'
import type { FailedArticle, LogEntry, LogPayload } from '../api/types'
import { useJobsStore } from '../stores/jobs'

const jobs = useJobsStore()

const limit = ref(0)
const force = ref(false)
const failures = ref<FailedArticle[]>([])
const systemLogs = ref<LogEntry[]>([])
let logTimer: number | null = null
let logSeq = 0

const statusMeta: Record<string, { label: string; type: 'success' | 'danger' | 'warning' | 'info' }> = {
  done: { label: '成功', type: 'success' },
  error: { label: '失败', type: 'danger' },
  failed: { label: '失败', type: 'danger' },
  skipped: { label: '跳过', type: 'warning' },
  cancelled: { label: '已取消', type: 'info' },
}

const levelType: Record<string, 'danger' | 'warning' | 'info'> = {
  CRITICAL: 'danger',
  ERROR: 'danger',
  WARNING: 'warning',
}

const stateLabel = computed(() => {
  const state = jobs.job?.state
  if (!state) return '空闲'
  return { running: '运行中', finished: '已完成', cancelled: '已取消', error: '失败' }[state] ?? state
})

const stateType = computed(() => {
  const state = jobs.job?.state
  if (state === 'running') return 'primary'
  if (state === 'finished') return 'success'
  if (state === 'cancelled') return 'info'
  if (state === 'error') return 'danger'
  return 'info'
})

const stageLabel = computed(() => {
  const stage = jobs.job?.stage
  if (stage === 'knowledge_graph') return '更新知识图谱'
  if (stage === 'related') return '计算文章关联'
  return ''
})

async function start(): Promise<void> {
  await jobs.startFetch({ limit: limit.value || 0, force: force.value })
}

async function loadFailures(): Promise<void> {
  try {
    failures.value = await apiGet<FailedArticle[]>('/api/failed')
  } catch {
    failures.value = []
  }
}

async function retryAll(): Promise<void> {
  try {
    await loadFailures()
    if (!failures.value.length) {
      ElMessage.info('没有失败文章需要重试')
      return
    }
    await apiPost('/api/articles/retry', {
      article_ids: failures.value.map((item) => item.article_id),
    })
    ElMessage.success(`已提交 ${failures.value.length} 篇重试`)
    await jobs.refresh()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

async function retryOne(article: FailedArticle): Promise<void> {
  try {
    await apiPost('/api/articles/retry', { article_ids: [article.article_id] })
    ElMessage.success('已提交重试')
    await jobs.refresh()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

/** 轮询系统日志（WARNING 以上）：终端看不到的告警在界面上必须能看到。 */
async function pollLogs(): Promise<void> {
  try {
    const payload = await apiGet<LogPayload>(`/api/logs?since=${logSeq}`)
    if (payload.entries.length) {
      systemLogs.value = [...systemLogs.value, ...payload.entries].slice(-100)
      logSeq = payload.last_seq
    }
  } catch {
    // 日志轮询失败不该打扰用户
  }
}

onMounted(() => {
  void jobs.refresh()
  void loadFailures()
  void pollLogs()
  logTimer = window.setInterval(pollLogs, 3000)
})

onUnmounted(() => {
  if (logTimer !== null) {
    window.clearInterval(logTimer)
  }
})
</script>

<template>
  <div class="run">
    <div class="run__head">
      <h2 class="run__title">文章抓取</h2>
      <el-tag :type="stateType">{{ stateLabel }}</el-tag>
    </div>

    <el-alert v-if="jobs.error" :title="jobs.error" type="error" show-icon :closable="false" class="mb" />

    <el-card shadow="never" class="mb">
      <div class="controls">
        <div class="controls__field">
          <span class="controls__label">最多处理</span>
          <el-input-number v-model="limit" :min="0" :max="200" :disabled="jobs.running" />
          <span class="controls__hint">0 = 不限</span>
        </div>
        <el-checkbox v-model="force" :disabled="jobs.running">强制重新处理已处理过的文章</el-checkbox>
        <div class="controls__actions">
          <el-button type="primary" :loading="jobs.running" :disabled="jobs.running" @click="start">
            开始抓取
          </el-button>
          <el-button type="danger" :disabled="!jobs.running" @click="jobs.cancel()">取消</el-button>
          <el-button :disabled="jobs.running" @click="retryAll">重试失败文章</el-button>
        </div>
      </div>
    </el-card>

    <el-card v-if="jobs.job" shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>进度</span>
          <span class="muted">
            {{ jobs.job.completed }} / {{ jobs.job.total || '?' }}
            <template v-if="stageLabel"> · {{ stageLabel }}</template>
          </span>
        </div>
      </template>
      <el-progress :percentage="jobs.percent" :status="jobs.job.state === 'error' ? 'exception' : undefined" />
      <div v-if="jobs.job.last_title" class="run__current">当前：{{ jobs.job.last_title }}</div>
      <div v-if="jobs.job.error" class="run__error">{{ jobs.job.error }}</div>
    </el-card>

    <el-card v-if="jobs.items.length" shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>本次结果</span>
          <span class="muted">
            成功 {{ jobs.summary.done ?? 0 }} · 失败 {{ jobs.summary.failed ?? 0 }}
          </span>
        </div>
      </template>
      <el-table :data="jobs.items" size="small" max-height="320">
        <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="120" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusMeta[row.status]?.type ?? 'info'" size="small">
              {{ statusMeta[row.status]?.label ?? row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="原因" min-width="160" show-overflow-tooltip />
      </el-table>
    </el-card>

    <el-card v-if="failures.length" shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>失败文章</span>
          <span class="muted">共 {{ failures.length }} 篇</span>
        </div>
      </template>
      <el-table :data="failures" size="small" max-height="260">
        <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="error" label="原因" min-width="200" show-overflow-tooltip />
        <el-table-column prop="retry_count" label="重试" width="70" align="center" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="retryOne(row)">重试</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="jobs.logLines.length" shadow="never" class="mb">
      <template #header><span>运行日志</span></template>
      <pre class="log">{{ jobs.logLines.join('\n') }}</pre>
    </el-card>

    <el-card v-if="systemLogs.length" shadow="never">
      <template #header>
        <div class="card__header">
          <span>系统告警（WARNING 以上）</span>
          <el-button link size="small" @click="systemLogs = []">清空</el-button>
        </div>
      </template>
      <div v-for="entry in systemLogs" :key="entry.seq" class="syslog">
        <el-tag :type="levelType[entry.level] ?? 'info'" size="small">{{ entry.level }}</el-tag>
        <span class="syslog__time">{{ new Date(entry.ts).toLocaleTimeString('zh-CN') }}</span>
        <span class="syslog__logger">{{ entry.logger }}</span>
        <span class="syslog__message">{{ entry.message }}</span>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.run__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.run__title {
  margin: 0;
  font-size: 18px;
}

.mb {
  margin-bottom: 16px;
}

.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
}

.controls__field {
  display: flex;
  align-items: center;
  gap: 8px;
}

.controls__label {
  font-size: 14px;
}

.controls__hint {
  font-size: 12px;
  color: var(--wx-muted);
}

.controls__actions {
  margin-left: auto;
}

.card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.muted {
  font-size: 12px;
  color: var(--wx-muted);
}

.run__current {
  margin-top: 10px;
  font-size: 13px;
}

.run__error {
  margin-top: 10px;
  font-size: 13px;
  color: #f56c6c;
}

.log {
  margin: 0;
  max-height: 260px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.syslog {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px dashed #f0f2f5;
}

.syslog__time {
  color: var(--wx-muted);
  font-variant-numeric: tabular-nums;
}

.syslog__logger {
  color: var(--wx-muted);
}

.syslog__message {
  flex: 1;
  word-break: break-all;
}
</style>
