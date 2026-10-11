<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { apiGet, apiPost, errorMessage } from '../api/client'
import type { AccountStatus } from '../api/types'

const account = ref<AccountStatus | null>(null)
const loading = ref(false)
const refreshing = ref(false)
const error = ref('')

/** 未配置令牌时上游只能给 401 —— 页面必须说「查不到」而不是「未登录」 */
const tokenConfigured = computed(() => account.value?.token.configured ?? false)
const tokenExpired = computed(() => account.value?.token.expired ?? false)

/**
 * 取一个访问令牌的一句话命令。
 * 刻意不放凭据：账号密码在 deploy/we-mp-rss/.env 里，界面只给占位符。
 */
const tokenCommand = `curl -s -X POST http://localhost:8001/api/v1/wx/auth/token \\
  -d "username=<WERSS_USER>&password=<WERSS_PASS>" \\
  | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])"`

const copied = ref(false)

async function copyCommand(): Promise<void> {
  try {
    await navigator.clipboard.writeText(tokenCommand)
    copied.value = true
    ElMessage.success('命令已复制，凭据见 deploy/we-mp-rss/.env')
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    ElMessage.warning('浏览器拒绝了剪贴板访问，请手动选中复制')
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    account.value = await apiGet<AccountStatus>('/api/source/account')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function refresh(): Promise<void> {
  refreshing.value = true
  try {
    const result = await apiPost<{ ok: boolean; error: string; account: AccountStatus }>(
      '/api/source/refresh',
    )
    if (result.account) {
      account.value = result.account
    }
    // 这个按钮**不能**真的刷新微信读书登录态（Cookie 由 we-mp-rss 自己维护），
    // 所以提示只说它实际做了什么：重新探测 + 重读状态
    if (!result.account?.token.configured) {
      ElMessage.warning('未配置访问令牌，读不到真实登录态；请按下面的命令取一个令牌并填入配置')
    } else if (!result.account.queried) {
      ElMessage.error(
        result.account.token.expired
          ? '访问令牌已过期，读不到登录态；请重新取一个'
          : '读不到登录态：令牌无效或 we-mp-rss 不可达',
      )
    } else if (result.account.is_logged_in) {
      ElMessage.success('已重新读取：微信读书处于登录状态')
    } else {
      ElMessage.warning('已重新读取：微信读书未登录，请到授权页面重新扫码')
    }
    if (!result.ok) {
      ElMessage.error(`we-mp-rss 不可达${result.error ? `：${result.error}` : ''}`)
    }
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
          <el-tag
            v-if="account"
            :type="account.queried ? (account.is_logged_in ? 'success' : 'danger') : 'info'"
            size="small"
          >
            {{ account.queried ? (account.is_logged_in ? '已登录' : '未登录') : '查不到' }}
          </el-tag>
        </div>
      </template>

      <el-descriptions v-if="account" :column="1" size="small" border>
        <el-descriptions-item label="账号">{{ account.username || '—' }}</el-descriptions-item>
        <el-descriptions-item label="过期时间">
          {{ account.queried ? formatTime(account.expire_at) : '未知' }}
        </el-descriptions-item>
        <el-descriptions-item label="需要续期">
          <el-tag
            v-if="account.queried"
            :type="account.need_refresh ? 'warning' : 'success'"
            size="small"
          >
            {{ account.need_refresh ? '是' : '否' }}
          </el-tag>
          <el-tag v-else type="info" size="small">未知</el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <div class="actions">
        <el-button type="primary" :loading="refreshing" @click="refresh">重新读取状态</el-button>
        <el-button @click="openLogin">打开授权页面</el-button>
      </div>

      <el-alert
        v-if="tokenExpired"
        class="mt"
        type="error"
        :closable="false"
        show-icon
        title="数据源访问令牌已过期，登录态/公众号列表都读不到，需要重新取一个令牌"
      />

      <div class="hint">
        登录态由 we-mp-rss 自行维护（微信读书 Cookie），本项目不持有凭据，因此这里只能<strong>读取</strong>、
        不能刷新。失败时请到 <code>http://localhost:8001</code> 重新登录。
      </div>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card__header">
          <span>数据源访问令牌</span>
          <el-tag :type="tokenConfigured && !tokenExpired ? 'success' : 'warning'" size="small">
            {{ tokenConfigured ? (tokenExpired ? '已过期' : '已配置') : '未配置' }}
          </el-tag>
        </div>
      </template>

      <p class="token-lead">
        本页的登录态、以及「公众号管理」的订阅列表，都走 we-mp-rss 的管理接口，需要 Bearer
        令牌。<strong>文章抓取本身不需要它</strong>（走公开的 <code>/feed/*.json</code>）。
      </p>

      <el-descriptions v-if="tokenConfigured" :column="1" size="small" border class="mb">
        <el-descriptions-item label="令牌有效期至">
          {{ formatTime(account?.token.expires_at || '') }}
        </el-descriptions-item>
      </el-descriptions>

      <p class="token-lead">取一个令牌（账号密码见 <code>deploy/we-mp-rss/.env</code>）：</p>
      <pre class="cmd">{{ tokenCommand }}</pre>
      <div class="actions">
        <el-button size="small" @click="copyCommand">{{ copied ? '已复制' : '复制命令' }}</el-button>
        <span class="hint hint--inline">
          把输出粘到「配置管理 → 文章数据源 → 访问令牌」并保存。有效期由 we-mp-rss 的
          <code>token_expire_minutes</code> 决定（本部署配方是 3 天），到期后本页会提示。
        </span>
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
  align-items: center;
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

.hint--inline {
  margin-top: 0;
  flex: 1;
}

.token-lead {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.6;
}

.cmd {
  margin: 0;
  padding: 10px 12px;
  background: #f6f8fa;
  border: 1px solid var(--wx-border);
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre;
}
</style>
