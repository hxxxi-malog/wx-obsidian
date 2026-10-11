<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { apiGet, apiPost, errorMessage } from '../api/client'
import type { AccountStatus, ConfigSnapshot } from '../api/types'

const account = ref<AccountStatus | null>(null)
const loading = ref(false)
const refreshing = ref(false)
const error = ref('')
const tokenConfigured = ref(true)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    account.value = await apiGet<AccountStatus>('/api/source/account')
    // 上游查询登录态同样需要 Bearer Token；未配置时适配器只能返回「未登录」，
    // 这里必须区分「真的没登录」和「查不到」，否则用户会去排查一个不存在的问题
    const config = await apiGet<ConfigSnapshot>('/api/config')
    tokenConfigured.value = Boolean(config.source.token)
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function refresh(): Promise<void> {
  refreshing.value = true
  try {
    const result = await apiPost<{ ok: boolean }>('/api/source/refresh')
    if (result.ok) {
      ElMessage.success('登录态已刷新')
    } else {
      ElMessage.error('刷新失败，可能需要重新扫码登录')
    }
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    refreshing.value = false
  }
}

async function openLogin(): Promise<void> {
  try {
    const { url } = await apiGet<{ url: string }>('/api/source/login-url')
    if (!url) {
      ElMessage.warning('数据源未提供授权页面地址')
      return
    }
    window.open(url, '_blank', 'noopener')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

function formatTime(raw: string | null): string {
  if (!raw) return '未知'
  const parsed = new Date(raw)
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString('zh-CN')
}

onMounted(load)
</script>

<template>
  <div class="account">
    <div class="account__head">
      <h2 class="account__title">账号管理</h2>
      <el-button :loading="loading" @click="load">刷新状态</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>微信读书登录态</span>
          <el-tag v-if="account" :type="account.is_logged_in ? 'success' : 'danger'" size="small">
            {{ account.is_logged_in ? '已登录' : '未登录' }}
          </el-tag>
        </div>
      </template>

      <el-descriptions v-if="account" :column="1" size="small" border>
        <el-descriptions-item label="账号">{{ account.username || '—' }}</el-descriptions-item>
        <el-descriptions-item label="过期时间">{{ formatTime(account.expire_at) }}</el-descriptions-item>
        <el-descriptions-item label="需要续期">
          <el-tag :type="account.need_refresh ? 'warning' : 'success'" size="small">
            {{ account.need_refresh ? '是' : '否' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <div class="actions">
        <el-button type="primary" :loading="refreshing" @click="refresh">刷新登录态</el-button>
        <el-button @click="openLogin">打开授权页面</el-button>
      </div>

      <el-alert
        v-if="account && !account.is_logged_in && !tokenConfigured"
        class="mt"
        type="info"
        :closable="false"
        show-icon
        title="未配置数据源访问令牌，无法查询真实登录态（显示为「未登录」并不代表登录已失效）"
      />

      <div class="hint">
        登录态由 we-mp-rss 自行维护（微信读书 Cookie）。这里只做探测与提示，
        失败时请到 <code>http://localhost:8001</code> 重新登录。
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.account__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.account__title {
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

.actions {
  margin-top: 16px;
  display: flex;
  gap: 8px;
}

.mt {
  margin-top: 12px;
}

.hint {
  margin-top: 12px;
  font-size: 12px;
  color: var(--wx-muted);
}
</style>
