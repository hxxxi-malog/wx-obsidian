<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError, apiGet } from '../api/client'
import type { HealthPayload, StatsPayload } from '../api/types'

const health = ref<HealthPayload | null>(null)
const stats = ref<StatsPayload | null>(null)
const loading = ref(false)
const error = ref('')

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [healthPayload, statsPayload] = await Promise.all([
      apiGet<HealthPayload>('/api/health'),
      apiGet<StatsPayload>('/api/stats'),
    ])
    health.value = healthPayload
    stats.value = statsPayload
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      error.value = '未授权：请从应用启动器打开的地址进入（URL 需带 token）'
    } else {
      error.value = err instanceof Error ? err.message : String(err)
    }
  } finally {
    loading.value = false
  }
}

/** 四项组件状态：null 表示尚未配置 */
const componentRows = computed(() => {
  const payload = health.value
  if (!payload) {
    return []
  }
  return [
    { label: '数据源 (we-mp-rss)', result: payload.source },
    { label: 'LLM API', result: payload.llm_api },
    { label: 'Vision API', result: payload.vision_api },
    { label: 'Vault 路径', result: payload.vault_path },
  ]
})

const statCells = computed(() => {
  const payload = stats.value
  if (!payload) {
    return []
  }
  return [
    { label: '已处理', value: payload.processed_articles },
    { label: '失败', value: payload.failed_articles },
    { label: '合计', value: payload.total_articles },
    { label: '分类数', value: Object.keys(payload.categories).length },
  ]
})

const categoryRows = computed(() => {
  const entries = Object.entries(stats.value?.categories ?? {})
  const max = Math.max(1, ...entries.map(([, count]) => count))
  return entries
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => ({ name, count, percent: Math.round((count / max) * 100) }))
})

const lastFetch = computed(() => {
  const raw = stats.value?.last_fetch_time
  if (!raw) {
    return '暂无记录'
  }
  const parsed = new Date(raw)
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString('zh-CN')
})

onMounted(load)
</script>

<template>
  <div class="dashboard">
    <div class="dashboard__head">
      <h2 class="dashboard__title">状态总览</h2>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>组件状态</span>
          <el-tag v-if="health" :type="health.overall ? 'success' : 'danger'" size="small">
            {{ health.overall ? '全部正常' : '存在异常' }}
          </el-tag>
        </div>
      </template>

      <el-empty v-if="!componentRows.length" description="正在读取…" :image-size="60" />
      <ul v-else class="status-list">
        <li v-for="row in componentRows" :key="row.label" class="status-list__row">
          <span class="status-list__label">{{ row.label }}</span>
          <span v-if="!row.result" class="status-list__muted">未配置</span>
          <span v-else class="status-list__right">
            <span class="status-list__muted">{{ row.result.message }}</span>
            <span class="status-list__latency">{{ Math.round(row.result.latency_ms) }} ms</span>
            <el-tag :type="row.result.success ? 'success' : 'danger'" size="small">
              {{ row.result.success ? '正常' : '异常' }}
            </el-tag>
          </span>
        </li>
      </ul>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>处理统计</span>
          <span class="status-list__muted">上次抓取：{{ lastFetch }}</span>
        </div>
      </template>

      <div class="stats">
        <div v-for="cell in statCells" :key="cell.label" class="stats__cell">
          <div class="stats__value">{{ cell.value }}</div>
          <div class="stats__label">{{ cell.label }}</div>
        </div>
      </div>

      <div class="categories">
        <div class="categories__title">分类分布</div>
        <el-empty v-if="!categoryRows.length" description="暂无数据" :image-size="50" />
        <div v-for="row in categoryRows" :key="row.name" class="categories__row">
          <span class="categories__name">{{ row.name }}</span>
          <span class="categories__bar">
            <span class="categories__fill" :style="{ width: `${row.percent}%` }" />
          </span>
          <span class="categories__count">{{ row.count }}</span>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.dashboard__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.dashboard__title {
  margin: 0;
  font-size: 18px;
}

.mb {
  margin-bottom: 16px;
}

.card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.status-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.status-list__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid #f0f2f5;
  font-size: 14px;
}

.status-list__row:last-child {
  border-bottom: none;
}

.status-list__label {
  font-weight: 500;
}

.status-list__right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-list__muted {
  color: var(--wx-muted);
  font-size: 12px;
}

.status-list__latency {
  color: var(--wx-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.stats {
  display: flex;
  gap: 12px;
}

.stats__cell {
  flex: 1;
  padding: 12px;
  background: #fafafa;
  border-radius: 6px;
  text-align: center;
}

.stats__value {
  font-size: 24px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.stats__label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--wx-muted);
}

.categories {
  margin-top: 20px;
}

.categories__title {
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--wx-muted);
}

.categories__row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0;
  font-size: 13px;
}

.categories__name {
  width: 120px;
}

.categories__bar {
  flex: 1;
  height: 8px;
  background: #f0f2f5;
  border-radius: 4px;
  overflow: hidden;
}

.categories__fill {
  display: block;
  height: 100%;
  background: #409eff;
}

.categories__count {
  width: 40px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
