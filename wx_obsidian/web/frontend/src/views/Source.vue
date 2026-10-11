<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { apiGet, errorMessage } from '../api/client'
import type { SourceHealth } from '../api/types'

const health = ref<SourceHealth | null>(null)
const loading = ref(false)
const error = ref('')

const DOCKER_COMMANDS = [
  'cd deploy/we-mp-rss',
  'cp .env.example .env   # 填 WERSS_PASS / WEREAD_COOKIE',
  'docker compose up -d --build',
]

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    health.value = await apiGet<SourceHealth>('/api/source/health')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择文本')
  }
}

onMounted(load)
</script>

<template>
  <div class="source">
    <div class="source__head">
      <h2 class="source__title">数据源状态</h2>
      <el-button :loading="loading" @click="load">刷新状态</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>we-mp-rss 服务</span>
          <el-tag v-if="health" :type="health.healthy ? 'success' : 'danger'" size="small">
            {{ health.healthy ? '运行中' : '未运行' }}
          </el-tag>
        </div>
      </template>

      <el-descriptions v-if="health" :column="1" size="small" border>
        <el-descriptions-item label="数据源类型">{{ health.type || '—' }}</el-descriptions-item>
        <el-descriptions-item label="服务地址">{{ health.base_url || '—' }}</el-descriptions-item>
      </el-descriptions>

      <el-alert
        v-if="health && !health.healthy"
        class="mt"
        type="warning"
        :closable="false"
        show-icon
        title="服务不可达。抓取会失败，请先按下方的命令把数据源跑起来。"
      />
    </el-card>

    <el-card shadow="never">
      <template #header><span>启动数据源（Docker）</span></template>
      <div v-for="command in DOCKER_COMMANDS" :key="command" class="command">
        <code class="command__text">{{ command }}</code>
        <el-button link type="primary" size="small" @click="copy(command)">复制</el-button>
      </div>
      <div class="hint">
        启动后访问 <code>http://localhost:8001</code> 完成初始化并登录微信读书。
        本应用不代为管理容器，只做探活。
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.source__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.source__title {
  margin: 0;
  font-size: 18px;
}

.mb {
  margin-bottom: 16px;
}

.mt {
  margin-top: 12px;
}

.card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.command {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--wx-border);
}

.command__text {
  font-size: 13px;
  word-break: break-all;
}

.hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--wx-muted);
}
</style>
