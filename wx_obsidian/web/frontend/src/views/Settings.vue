<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { apiGet, apiPut, apiPost, errorMessage } from '../api/client'
import type { ConfigSaveResult, ConfigSnapshot, ConnectionTestResult } from '../api/types'
import DirPicker from '../components/DirPicker.vue'

const config = ref<ConfigSnapshot | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const pickerVisible = ref(false)

/** 表单只承载可编辑字段；密钥留空表示「不修改」 */
const form = ref({
  llmActive: '',
  llmKey: '',
  llmBaseUrl: '',
  llmModel: '',
  visionActive: '',
  visionKey: '',
  visionBaseUrl: '',
  visionModel: '',
  visionEnabled: true,
  sourceType: '',
  sourceBaseUrl: '',
  sourceToken: '',
  vaultPath: '',
  maxDays: 7,
  maxWorkers: 5,
})

const testing = ref<Record<string, boolean>>({})
const testResult = ref<Record<string, ConnectionTestResult>>({})

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await apiGet<ConfigSnapshot>('/api/config')
    config.value = data
    form.value = {
      llmActive: data.llm.active,
      llmKey: '',
      llmBaseUrl: data.llm.effective.base_url,
      llmModel: data.llm.effective.model,
      visionActive: data.vision.active,
      visionKey: '',
      visionBaseUrl: data.vision.effective.base_url,
      visionModel: data.vision.effective.model,
      visionEnabled: data.vision.enabled,
      sourceType: data.source.type,
      sourceBaseUrl: data.source.base_url,
      sourceToken: '',
      vaultPath: data.obsidian.vault_path,
      maxDays: data.fetch.max_days,
      maxWorkers: data.fetch.max_workers,
    }
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function activeProvider(): string {
  return form.value.llmActive || config.value?.llm.active || ''
}

function visionProvider(): string {
  return form.value.visionActive || config.value?.vision.active || ''
}

async function save(): Promise<void> {
  saving.value = true
  const llm = activeProvider()
  const vision = visionProvider()

  const patch: Record<string, string | number | boolean> = {
    'llm.active': llm,
    'vision.active': vision,
    'vision.enabled': form.value.visionEnabled,
    'source.type': form.value.sourceType,
    'source.base_url': form.value.sourceBaseUrl,
    'obsidian.vault_path': form.value.vaultPath,
    'fetch.max_days': form.value.maxDays,
    'fetch.max_workers': form.value.maxWorkers,
  }
  if (llm) {
    patch[`llm.providers.${llm}.base_url`] = form.value.llmBaseUrl
    patch[`llm.providers.${llm}.model`] = form.value.llmModel
  }
  if (vision) {
    patch[`vision.providers.${vision}.base_url`] = form.value.visionBaseUrl
    patch[`vision.providers.${vision}.model`] = form.value.visionModel
  }
  if (form.value.sourceToken) {
    patch['source.token'] = form.value.sourceToken
  }

  // 密钥留空则跳过，避免把已存的 Key 清掉
  const env: Record<string, string> = {}
  if (form.value.llmKey) env['DEEPSEEK_API_KEY'] = form.value.llmKey
  if (form.value.visionKey) env['VISION_API_KEY'] = form.value.visionKey

  try {
    const result = await apiPut<ConfigSaveResult>('/api/config', { patch, env })
    ElMessage.success(`已保存 ${result.applied.length} 项`)
    if (result.restart_required) {
      ElMessage.warning('数据源地址改动需重启服务后生效')
    }
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function test(target: 'llm' | 'vision' | 'source' | 'all'): Promise<void> {
  testing.value = { ...testing.value, [target]: true }
  try {
    const result = await apiPost<Record<string, ConnectionTestResult>>('/api/config/test', { target })
    testResult.value = { ...testResult.value, ...result }
    const failed = Object.entries(result).filter(([, value]) => !value.success)
    if (failed.length) {
      ElMessage.warning(failed.map(([name, value]) => `${name}: ${value.message}`).join('；'))
    } else {
      ElMessage.success('连通性正常')
    }
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    testing.value = { ...testing.value, [target]: false }
  }
}

function onPicked(path: string): void {
  form.value.vaultPath = path
}

onMounted(load)
</script>

<template>
  <div class="settings">
    <div class="settings__head">
      <h2 class="settings__title">配置管理</h2>
      <el-button :loading="loading" @click="load">重新载入</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />
    <el-alert
      title="密钥留空表示不修改；已保存的 Key 只会以掩码形式回显"
      type="info"
      :closable="false"
      class="mb"
    />

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>语言模型 (LLM)</span>
          <div class="card__actions">
            <el-tag v-if="config" size="small" effect="plain">
              当前生效：{{ config.llm.effective.model || '—' }}
            </el-tag>
            <el-button size="small" :loading="testing.llm" @click="test('llm')">测试连通性</el-button>
          </div>
        </div>
      </template>

      <el-form label-width="110px" label-position="left">
        <el-form-item label="生效渠道">
          <el-select v-model="form.llmActive" allow-create filterable class="w-220">
            <el-option v-for="name in config?.llm.available ?? []" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.llmKey"
            type="password"
            show-password
            :placeholder="config?.llm.api_key || '留空则不修改'"
          />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.llmBaseUrl" />
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.llmModel" />
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>视觉模型 (Vision)</span>
          <div class="card__actions">
            <el-tag v-if="config" size="small" effect="plain">
              当前生效：{{ config.vision.effective.model || '—' }}
            </el-tag>
            <el-button size="small" :loading="testing.vision" @click="test('vision')">测试连通性</el-button>
          </div>
        </div>
      </template>

      <el-form label-width="110px" label-position="left">
        <el-form-item label="启用">
          <el-switch v-model="form.visionEnabled" />
        </el-form-item>
        <el-form-item label="生效渠道">
          <el-select v-model="form.visionActive" allow-create filterable class="w-220">
            <el-option
              v-for="name in config?.vision.available ?? []"
              :key="name"
              :label="name"
              :value="name"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.visionKey"
            type="password"
            show-password
            :placeholder="config?.vision.api_key || '留空则用渠道声明的环境变量'"
          />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.visionBaseUrl" />
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.visionModel" />
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>文章数据源</span>
          <el-button size="small" :loading="testing.source" @click="test('source')">测试连通性</el-button>
        </div>
      </template>

      <el-form label-width="110px" label-position="left">
        <el-form-item label="类型">
          <el-input v-model="form.sourceType" />
        </el-form-item>
        <el-form-item label="服务地址">
          <el-input v-model="form.sourceBaseUrl" />
        </el-form-item>
        <el-form-item label="访问令牌">
          <el-input
            v-model="form.sourceToken"
            type="password"
            show-password
            :placeholder="config?.source.token || '留空则不修改'"
          />
        </el-form-item>
      </el-form>
      <div class="hint">数据源地址改动需重启服务后生效</div>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header><span>知识库与抓取</span></template>

      <el-form label-width="110px" label-position="left">
        <el-form-item label="Vault 路径">
          <div class="vault">
            <el-input v-model="form.vaultPath" />
            <el-button @click="pickerVisible = true">浏览…</el-button>
          </div>
        </el-form-item>
        <el-form-item label="文章目录">
          <el-input :model-value="config?.obsidian.articles_dir" disabled />
        </el-form-item>
        <el-form-item label="最大抓取天数">
          <el-input-number v-model="form.maxDays" :min="1" :max="365" />
        </el-form-item>
        <el-form-item label="最大并行数">
          <el-input-number v-model="form.maxWorkers" :min="1" :max="32" />
        </el-form-item>
      </el-form>
    </el-card>

    <div class="settings__footer">
      <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
    </div>

    <DirPicker v-model="pickerVisible" :start-path="form.vaultPath" @picked="onPicked" />
  </div>
</template>

<style scoped>
.settings__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.settings__title {
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

.card__actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.w-220 {
  width: 220px;
}

.vault {
  display: flex;
  gap: 8px;
  width: 100%;
}

.hint {
  font-size: 12px;
  color: var(--wx-muted);
}

.settings__footer {
  display: flex;
  justify-content: flex-end;
}
</style>
