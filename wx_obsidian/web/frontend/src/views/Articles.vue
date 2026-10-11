<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiDelete, apiGet, apiPost, buildQuery, errorMessage } from '../api/client'
import type { ArticleItem, ArticleList } from '../api/types'

const items = ref<ArticleItem[]>([])
const categories = ref<string[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const query = ref('')
const category = ref('')
const status = ref('')
const loading = ref(false)
const error = ref('')

const statusMeta: Record<string, { label: string; type: 'success' | 'danger' | 'warning' | 'info' }> = {
  done: { label: '已处理', type: 'success' },
  error: { label: '失败', type: 'danger' },
  failed: { label: '失败', type: 'danger' },
  skipped: { label: '跳过', type: 'warning' },
  cancelled: { label: '已取消', type: 'info' },
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await apiGet<ArticleList>(
      `/api/articles${buildQuery({
        q: query.value,
        category: category.value,
        status: status.value,
        limit: pageSize.value,
        offset: (page.value - 1) * pageSize.value,
      })}`,
    )
    items.value = data.items
    categories.value = data.categories
    total.value = data.total
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function search(): void {
  page.value = 1
  void load()
}

function resetFilters(): void {
  query.value = ''
  category.value = ''
  status.value = ''
  search()
}

function onSizeChange(): void {
  page.value = 1
  void load()
}

async function remove(article: ArticleItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除「${article.title.slice(0, 40)}」？关联的 MOC 条目、概念页引用与归档记录会一并清理。`,
      '级联删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result = await apiDelete<{ actions: string[] }>(`/api/articles/${article.article_id}`)
    ElMessage.success(`已清理 ${result.actions.length} 项`)
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

async function openInObsidian(article: ArticleItem): Promise<void> {
  try {
    await apiPost(`/api/articles/${article.article_id}/open`)
    ElMessage.success('已在 Obsidian 中打开')
  } catch (err) {
    // 失败原因是多行可执行指引（例如「仓库未注册，请选择 …」），用对话框完整展示
    await ElMessageBox.alert(errorMessage(err), '无法在 Obsidian 中打开', {
      confirmButtonText: '知道了',
      customStyle: { whiteSpace: 'pre-line', maxWidth: '560px' },
    }).catch(() => undefined)
  }
}

function formatTime(raw: string): string {
  if (!raw) return '—'
  const parsed = new Date(raw)
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString('zh-CN')
}

onMounted(load)
</script>

<template>
  <div class="articles">
    <div class="articles__head">
      <h2 class="articles__title">文章管理</h2>
      <span class="muted">共 {{ total }} 篇</span>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <el-card shadow="never" class="mb">
      <div class="filters">
        <el-input
          v-model="query"
          placeholder="搜索标题"
          clearable
          class="filters__search"
          @keyup.enter="search"
        />
        <el-select v-model="category" placeholder="全部分类" clearable class="filters__select">
          <el-option v-for="name in categories" :key="name" :label="name" :value="name" />
        </el-select>
        <el-select v-model="status" placeholder="全部状态" clearable class="filters__select">
          <el-option label="已处理" value="done" />
          <el-option label="失败" value="error" />
          <el-option label="跳过" value="skipped" />
        </el-select>
        <el-button type="primary" @click="search">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </el-card>

    <el-card shadow="never">
      <el-table v-loading="loading" :data="items" size="small" @row-dblclick="openInObsidian">
        <el-table-column prop="title" label="标题" min-width="260" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="130" />
        <el-table-column prop="sub_topic" label="子主题" width="140" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusMeta[row.status]?.type ?? 'info'" size="small">
              {{ statusMeta[row.status]?.label ?? row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="文件" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.file_exists" type="success" size="small" effect="plain">在库</el-tag>
            <el-tag v-else type="danger" size="small" effect="plain">缺失</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="处理时间" width="170">
          <template #default="{ row }">{{ formatTime(row.processed_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openInObsidian(row)">打开</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          layout="prev, pager, next, sizes, total"
          :page-sizes="[20, 50, 100]"
          :total="total"
          @current-change="load"
          @size-change="onSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.articles__head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 16px;
}

.articles__title {
  margin: 0;
  font-size: 18px;
}

.mb {
  margin-bottom: 16px;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.filters__search {
  width: 240px;
}

.filters__select {
  width: 150px;
}

.muted {
  font-size: 12px;
  color: var(--wx-muted);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
