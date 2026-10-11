<script setup lang="ts">
/**
 * 目录选择器。
 *
 * 浏览器沙箱拿不到本地绝对路径（webkitdirectory 只给相对路径），
 * 所以目录浏览必须走后端 /api/fs/list。
 */

import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { apiGet, errorMessage } from '../api/client'
import type { FsListing } from '../api/types'

const props = defineProps<{ modelValue: boolean; startPath: string }>()
const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'picked', path: string): void
}>()

const current = ref('')
const entries = ref<FsListing['entries']>([])
const parent = ref('')
const loading = ref(false)

async function browse(path?: string): Promise<void> {
  loading.value = true
  try {
    const data = await apiGet<FsListing>(`/api/fs/list?path=${encodeURIComponent(path ?? current.value)}`)
    current.value = data.path
    parent.value = data.parent
    entries.value = data.entries
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

function choose(): void {
  if (!current.value) {
    return
  }
  emit('picked', current.value)
  emit('update:modelValue', false)
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      current.value = props.startPath || ''
      void browse(props.startPath || undefined)
    }
  },
)
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="选择知识库目录"
    width="620px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="picker__bar">
      <el-button size="small" :disabled="!parent" @click="browse(parent)">上一级</el-button>
      <span class="picker__path">{{ current || '—' }}</span>
    </div>

    <div v-loading="loading" class="picker__list">
      <div
        v-for="entry in entries.filter((item) => item.is_dir)"
        :key="entry.name"
        class="picker__row"
        @click="browse(`${current}/${entry.name}`)"
      >
        📁 {{ entry.name }}
      </div>
      <el-empty v-if="!entries.some((item) => item.is_dir)" description="没有子目录" :image-size="50" />
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" @click="choose">选择当前目录</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.picker__bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.picker__path {
  flex: 1;
  font-size: 12px;
  color: var(--wx-muted);
  word-break: break-all;
}

.picker__list {
  height: 300px;
  overflow: auto;
  border: 1px solid var(--wx-border);
  border-radius: 4px;
  padding: 4px;
}

.picker__row {
  padding: 6px 10px;
  font-size: 13px;
  cursor: pointer;
  border-radius: 4px;
}

.picker__row:hover {
  background: #ecf5ff;
}
</style>
