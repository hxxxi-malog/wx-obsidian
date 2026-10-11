/**
 * 配置 store：服务端快照 + 本地草稿 + 保存。
 *
 * 关键约束（不能改，否则会清掉用户已存的密钥）：
 *  - 密钥字段留空 = 「不修改」，不是「清空」；
 *  - patch 的键名与后端 `_PLAIN_PATCH_KEYS` / `_PROVIDER_PATCH_RE` 白名单逐字对应；
 *  - env 只允许 DEEPSEEK_API_KEY / VISION_API_KEY（界面能填的两个）。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { apiGet, apiPost, apiPut, errorMessage } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type {
  ConfigPatchPayload,
  ConfigSaveResult,
  ConfigSnapshot,
  ConnectionTestResult,
  ConnectionTestTarget,
} from '@/lib/api/types'

export interface ConfigForm {
  llmActive: string
  llmKey: string
  llmBaseUrl: string
  llmModel: string
  visionEnabled: boolean
  visionActive: string
  visionKey: string
  visionBaseUrl: string
  visionModel: string
  sourceType: string
  sourceBaseUrl: string
  sourceToken: string
  vaultPath: string
  maxDays: number
  maxWorkers: number
  webPort: number
  webOpenBrowser: boolean
}

export function emptyForm(): ConfigForm {
  return {
    llmActive: '',
    llmKey: '',
    llmBaseUrl: '',
    llmModel: '',
    visionEnabled: true,
    visionActive: '',
    visionKey: '',
    visionBaseUrl: '',
    visionModel: '',
    sourceType: '',
    sourceBaseUrl: '',
    sourceToken: '',
    vaultPath: '',
    maxDays: 7,
    maxWorkers: 5,
    webPort: 8765,
    webOpenBrowser: true,
  }
}

export function formFromSnapshot(snapshot: ConfigSnapshot): ConfigForm {
  return {
    llmActive: snapshot.llm.active,
    llmKey: '',
    llmBaseUrl: snapshot.llm.effective.base_url,
    llmModel: snapshot.llm.effective.model,
    visionEnabled: snapshot.vision.enabled,
    visionActive: snapshot.vision.active,
    visionKey: '',
    visionBaseUrl: snapshot.vision.effective.base_url,
    visionModel: snapshot.vision.effective.model,
    sourceType: snapshot.source.type,
    sourceBaseUrl: snapshot.source.base_url,
    sourceToken: '',
    vaultPath: snapshot.obsidian.vault_path,
    maxDays: snapshot.fetch.max_days,
    maxWorkers: snapshot.fetch.max_workers,
    webPort: snapshot.web.port,
    webOpenBrowser: snapshot.web.open_browser,
  }
}

/**
 * 草稿 → PUT /api/config 的请求体（纯函数，单测覆盖）。
 * 空串与掩码由后端再次兜底，这里只负责「不把没填的键发出去」。
 */
export function buildConfigPayload(form: ConfigForm): ConfigPatchPayload {
  const patch: Record<string, string | number | boolean> = {
    'vision.enabled': form.visionEnabled,
    'source.type': form.sourceType,
    'source.base_url': form.sourceBaseUrl,
    'obsidian.vault_path': form.vaultPath,
    'fetch.max_days': form.maxDays,
    'fetch.max_workers': form.maxWorkers,
    'web.port': form.webPort,
    'web.open_browser': form.webOpenBrowser,
  }
  if (form.llmActive) {
    patch['llm.active'] = form.llmActive
    patch[`llm.providers.${form.llmActive}.base_url`] = form.llmBaseUrl
    patch[`llm.providers.${form.llmActive}.model`] = form.llmModel
  }
  if (form.visionActive) {
    patch['vision.active'] = form.visionActive
    patch[`vision.providers.${form.visionActive}.base_url`] = form.visionBaseUrl
    patch[`vision.providers.${form.visionActive}.model`] = form.visionModel
  }
  if (form.sourceToken) {
    patch['source.token'] = form.sourceToken
  }

  const env: Record<string, string> = {}
  if (form.llmKey) {
    env['DEEPSEEK_API_KEY'] = form.llmKey
  }
  if (form.visionKey) {
    env['VISION_API_KEY'] = form.visionKey
  }

  return { patch, env }
}

export const useConfigStore = defineStore('config', () => {
  const snapshot = ref<ConfigSnapshot | null>(null)
  const form = ref<ConfigForm>(emptyForm())
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const lastSave = ref<ConfigSaveResult | null>(null)
  const testing = ref<Partial<Record<ConnectionTestTarget, boolean>>>({})
  const testResults = ref<Partial<Record<string, ConnectionTestResult>>>({})

  const vaultPath = computed(() => snapshot.value?.obsidian.vault_path ?? '')

  async function load(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      const data = await apiGet<ConfigSnapshot>(endpoints.config.get())
      snapshot.value = data
      form.value = formFromSnapshot(data)
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function save(): Promise<ConfigSaveResult | null> {
    saving.value = true
    error.value = ''
    try {
      const payload = buildConfigPayload(form.value)
      const result = await apiPut<ConfigSaveResult>(endpoints.config.save(), payload)
      lastSave.value = result
      // 回读一次：密钥、effective、渠道列表都可能被后端规范化
      await load()
      return result
    } catch (err) {
      error.value = errorMessage(err)
      return null
    } finally {
      saving.value = false
    }
  }

  /** 连通性测试：就地保留最近一次结果（含失败），供每个分区展示 */
  async function test(target: ConnectionTestTarget): Promise<Record<string, ConnectionTestResult> | null> {
    testing.value = { ...testing.value, [target]: true }
    try {
      const result = await apiPost<Record<string, ConnectionTestResult>>(
        endpoints.config.test(),
        { target },
      )
      testResults.value = { ...testResults.value, ...result }
      return result
    } catch (err) {
      error.value = errorMessage(err)
      return null
    } finally {
      testing.value = { ...testing.value, [target]: false }
    }
  }

  return {
    snapshot,
    form,
    loading,
    saving,
    error,
    lastSave,
    testing,
    testResults,
    vaultPath,
    load,
    save,
    test,
  }
})
