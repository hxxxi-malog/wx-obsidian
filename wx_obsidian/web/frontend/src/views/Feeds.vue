<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiDelete, apiGet, apiPost, errorMessage } from '../api/client'
import type { ConfigSnapshot, FeedItem } from '../api/types'

const feeds = ref<FeedItem[]>([])
const articleUrl = ref('')
const loading = ref(false)
const adding = ref(false)
const error = ref('')
const tokenConfigured = ref(true)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    feeds.value = await apiGet<FeedItem[]>('/api/feeds')
    // 上游 /api/v1/wx/mps 需要 Bearer Token，未配置时只会返回空列表——
    // 这时要明确告诉用户原因，而不是给一个静默空页面
    const config = await apiGet<ConfigSnapshot>('/api/config')
    tokenConfigured.value = Boolean(config.source.token)
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function add(): Promise<void> {
  const url = articleUrl.value.trim()
  if (!url) {
    ElMessage.warning('请先粘贴一篇文章链接')
    return
  }
  adding.value = true
  try {
    const feed = await apiPost<FeedItem>('/api/feeds', { article_url: url })
    ElMessage.success(`已添加：${feed.name}`)
    articleUrl.value = ''
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    adding.value = false
  }
}

async function remove(feed: FeedItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定取消订阅「${feed.name}」？`, '取消订阅', {
      type: 'warning',
      confirmButtonText: '取消订阅',
      cancelButtonText: '返回',
    })
  } catch {
    return
  }
  try {
    await apiDelete(`/api/feeds/${feed.id}`)
    ElMessage.success('已取消订阅')
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
  <div class="feeds">
    <div class="feeds__head">
      <h2 class="feeds__title">公众号管理</h2>
      <el-button :loading="loading" @click="load">刷新列表</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <el-card shadow="never" class="mb">
      <template #header><span>按文章链接添加公众号</span></template>
      <div class="add">
        <el-input
          v-model="articleUrl"
          placeholder="https://mp.weixin.qq.com/s/..."
          clearable
          @keyup.enter="add"
        />
        <el-button type="primary" :loading="adding" @click="add">添加</el-button>
      </div>
      <div class="hint">
        该能力取决于数据源实现（we-mp-rss 不支持时需在其管理界面里订阅）
      </div>
    </el-card>

    <el-card shadow="never">
      <el-table v-loading="loading" :data="feeds" size="small">
        <el-table-column prop="name" label="公众号" min-width="200" show-overflow-tooltip />
        <el-table-column prop="intro" label="简介" min-width="240" show-overflow-tooltip />
        <el-table-column label="同步时间" width="170">
          <template #default="{ row }">{{ formatTime(row.sync_time) }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="170">
          <template #default="{ row }">{{ formatTime(row.update_time) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="remove(row)">取消订阅</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !feeds.length" :image-size="60">
        <template #description>
          <div class="empty">
            <p v-if="!tokenConfigured" class="empty__title">
              读不到订阅列表：数据源的订阅接口需要访问令牌
            </p>
            <p v-else class="empty__title">还没有订阅任何公众号</p>
            <p class="empty__hint">
              上游 <code>/api/v1/wx/mps</code> 需要 Bearer Token
              <template v-if="!tokenConfigured">（当前未配置）</template>。
              可在「配置管理 → 文章数据源 → 访问令牌」里填入，或直接到
              <code>http://localhost:8001</code> 查看与管理订阅。
            </p>
          </div>
        </template>
      </el-empty>
    </el-card>
  </div>
</template>

<style scoped>
.feeds__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.feeds__title {
  margin: 0;
  font-size: 18px;
}

.mb {
  margin-bottom: 16px;
}

.add {
  display: flex;
  gap: 8px;
}

.hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--wx-muted);
}

.empty {
  max-width: 520px;
  margin: 0 auto;
  text-align: left;
}

.empty__title {
  margin: 0 0 6px;
  font-size: 14px;
  color: #303133;
  text-align: center;
}

.empty__hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--wx-muted);
}
</style>
