<script setup lang="ts">
/**
 * 账号管理：微信读书登录态 + 数据源访问令牌。
 *
 * 两条语义必须严格保留（写错会直接误导用户）：
 *  1. **三态机**：`queried=false` 是「查不到」，不是「未登录」——
 *     未配置令牌时上游只会 401，把它显示成「未登录」会让人白跑一趟扫码；
 *  2. 本页的按钮**不能**刷新微信读书登录态（Cookie 由 we-mp-rss 自己维护），
 *     它只能重新探测 + 重读，所以提示只说它实际做了什么。
 */
import {
  PhArrowClockwise,
  PhArrowsClockwise,
  PhKey,
  PhShieldCheck,
  PhSignIn,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, ref } from 'vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppCopyButton from '@/components/ui/AppCopyButton.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import AppTag from '@/components/ui/AppTag.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import { ApiError, apiGet, apiPost } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { AccountStatus, SourceRefreshResult } from '@/lib/api/types'
import { formatCountdown, formatDateTime } from '@/lib/format'
import type { Tone } from '@/lib/status'

const toast = useToast()

const refreshing = ref(false)
const opening = ref(false)

const state = useAsync<AccountStatus>(
  async (signal) => {
    const data = await apiGet<AccountStatus>(endpoints.source.account(), signal)
    // 后端在账号接口异常时同样是 200 + {error, detail}：没有 token 字段就说明
    // 这不是一份账号状态，不能拿它去渲染「未配置」。
    if (!data.token) {
      throw new ApiError(200, '账号状态读不到：数据源没有返回令牌状态', 'account_unavailable')
    }
    return data
  },
  { immediate: true },
)

const account = computed(() => state.data.value)
const tokenConfigured = computed(() => account.value?.token?.configured ?? false)
const tokenExpired = computed(() => account.value?.token?.expired ?? false)

/** 登录态三态：查不到（info）≠ 未登录（danger） */
const loginMeta = computed<{ label: string; tone: Tone }>(() => {
  const value = account.value
  if (!value) {
    return { label: '未知', tone: 'neutral' }
  }
  if (!value.queried) {
    return { label: '查不到', tone: 'neutral' }
  }
  return value.is_logged_in ? { label: '已登录', tone: 'success' } : { label: '未登录', tone: 'danger' }
})

const tokenMeta = computed<{ label: string; tone: Tone }>(() => {
  if (!tokenConfigured.value) {
    return { label: '未配置', tone: 'neutral' }
  }
  return tokenExpired.value ? { label: '已过期', tone: 'danger' } : { label: '已配置', tone: 'success' }
})

/** 过期时间：查不到、或上游没给（非 JWT 令牌解析不出 exp）时都如实说「未知」 */
const expireLabel = computed(() => {
  const value = account.value
  if (!value?.queried || !value.expire_at) {
    return '未知'
  }
  return formatDateTime(value.expire_at)
})

const tokenExpiresAt = computed(() => account.value?.token?.expires_at || '')
const tokenExpireLabel = computed(() =>
  tokenExpiresAt.value ? formatDateTime(tokenExpiresAt.value) : '未知',
)
const tokenCountdownLabel = computed(() =>
  tokenExpiresAt.value ? formatCountdown(tokenExpiresAt.value) : '未知',
)

/**
 * 取一个访问令牌的一句话命令。
 * 刻意不放凭据：账号密码在 deploy/we-mp-rss/.env 里，界面只给占位符。
 */
const tokenCommand = `curl -s -X POST http://localhost:8001/api/v1/wx/auth/token \\
  -d "username=<WERSS_USER>&password=<WERSS_PASS>" \\
  | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])"`

async function refresh(): Promise<void> {
  refreshing.value = true
  try {
    const result = await apiPost<SourceRefreshResult>(endpoints.source.refresh())
    const next = result.account
    const token = next?.token
    // 响应里没有 token 字段说明账号快照是空的（上游异常）：保留上一次的已知状态，
    // 否则会把「读不到」误画成「未配置令牌」。
    if (next?.token) {
      state.data.value = next
    }

    // 五种分支逐条保留：先讲令牌，再讲查询结果，最后才是服务可达性
    if (!token?.configured) {
      toast.warn('未配置访问令牌，读不到真实登录态', '请按下面的命令取一个令牌并填入配置')
    } else if (!next?.queried) {
      toast.error(
        token?.expired
          ? '访问令牌已过期，读不到登录态；请重新取一个'
          : '读不到登录态：令牌无效或 we-mp-rss 不可达',
      )
    } else if (next?.is_logged_in) {
      toast.success('已重新读取：微信读书处于登录状态')
    } else {
      toast.warn('已重新读取：微信读书未登录，请到授权页面重新扫码')
    }

    if (!result.ok) {
      toast.error(`we-mp-rss 不可达${result.error ? `：${result.error}` : ''}`)
    }
  } catch (err) {
    toast.error(err)
  } finally {
    refreshing.value = false
  }
}

async function openLogin(): Promise<void> {
  opening.value = true
  try {
    const { url } = await apiGet<{ url: string }>(endpoints.source.loginUrl())
    if (!url) {
      toast.warn('数据源未提供授权页面地址')
      return
    }
    window.open(url, '_blank', 'noopener')
  } catch (err) {
    toast.error(err)
  } finally {
    opening.value = false
  }
}
</script>

<template>
  <div>
    <PageHeader
      title="账号管理"
      description="微信读书登录态由数据源自行维护；本页只读取，不代为登录。"
    >
      <template #actions>
        <AppButton variant="outline" size="sm" :loading="state.loading.value" @click="state.run()">
          <template #icon>
            <PhArrowClockwise :size="14" aria-hidden="true" />
          </template>
          刷新状态
        </AppButton>
      </template>
    </PageHeader>

    <!-- 令牌过期是「一切读不到」的总原因，置顶说清楚 -->
    <section
      v-if="tokenExpired"
      class="mb-5 flex items-start gap-3 rounded-panel border border-danger/30 bg-danger-soft px-4 py-3.5"
      role="alert"
    >
      <PhWarningCircle :size="18" class="mt-px shrink-0 text-danger" aria-hidden="true" />
      <p class="m-0 text-[13.5px] leading-relaxed text-ink">
        数据源访问令牌已过期，登录态/公众号列表都读不到，需要重新取一个令牌。
      </p>
    </section>

    <AppErrorState
      v-if="state.error.value"
      class="mb-5"
      :message="state.error.value"
      hint="状态读不到时不要按「未登录」处理——先修好接口，再判断登录态。"
      @retry="state.run()"
    />

    <!-- 登录态 -->
    <section class="panel mb-5 rounded-panel px-5 py-5">
      <header class="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div class="min-w-0">
          <h2 class="m-0 flex items-center gap-2 text-[14.5px] font-semibold text-ink">
            <PhShieldCheck :size="16" class="text-faint" aria-hidden="true" />
            微信读书登录态
          </h2>
          <p class="m-0 mt-1 text-xs leading-relaxed text-muted">
            登录态由 we-mp-rss 自行维护（微信读书 Cookie），本项目不持有凭据，因此这里只能读取、不能刷新。
          </p>
        </div>
        <AppTag :tone="loginMeta.tone" variant="outline">{{ loginMeta.label }}</AppTag>
      </header>

      <div v-if="state.loading.value && !account" class="flex flex-col gap-3">
        <AppSkeleton variant="stat" />
        <AppSkeleton variant="line" width="52%" />
      </div>

      <template v-else-if="account">
        <div class="flex flex-wrap items-center gap-3">
          <AppStatusDot
            :tone="loginMeta.tone"
            :size="10"
            :pulse="account.queried && account.is_logged_in"
          />
          <p
            class="m-0 text-[22px] leading-none font-semibold tracking-tight"
            :class="
              loginMeta.tone === 'success'
                ? 'text-success'
                : loginMeta.tone === 'danger'
                  ? 'text-danger'
                  : 'text-muted'
            "
          >
            {{ loginMeta.label }}
          </p>
          <span v-if="!account.queried" class="text-xs text-faint">
            （未配置令牌或上游 401——不代表未登录）
          </span>
        </div>

        <dl class="mt-4 grid gap-4 sm:grid-cols-3">
          <div class="flex flex-col gap-1">
            <dt class="text-[12.5px] text-faint">账号</dt>
            <dd class="m-0 font-mono text-[13.5px] text-ink">{{ account.username || '—' }}</dd>
          </div>
          <div class="flex flex-col gap-1">
            <dt class="text-[12.5px] text-faint">过期时间</dt>
            <dd class="m-0 nums font-mono text-[13.5px] text-ink">{{ expireLabel }}</dd>
          </div>
          <div class="flex flex-col gap-1">
            <dt class="text-[12.5px] text-faint">需要续期</dt>
            <dd class="m-0">
              <AppTag
                v-if="account.queried"
                :tone="account.need_refresh ? 'warning' : 'success'"
                variant="outline"
              >
                {{ account.need_refresh ? '是' : '否' }}
              </AppTag>
              <AppTag v-else tone="neutral" variant="outline">未知</AppTag>
            </dd>
          </div>
        </dl>

        <div class="mt-5 flex flex-wrap items-center gap-2">
          <AppButton variant="solid" :loading="refreshing" @click="refresh">
            <template #icon>
              <PhArrowsClockwise :size="14" aria-hidden="true" />
            </template>
            重新读取状态
          </AppButton>
          <AppButton variant="outline" :loading="opening" @click="openLogin">
            <template #icon>
              <PhSignIn :size="14" aria-hidden="true" />
            </template>
            打开授权页面
          </AppButton>
        </div>

        <p class="m-0 mt-4 border-t border-hairline pt-4 text-xs leading-relaxed text-muted">
          「重新读取状态」只重新探测服务可达性并重读登录态，不会替你刷新 Cookie。失败时请到
          <code>http://localhost:8001</code> 重新登录。
        </p>
      </template>

      <p v-else class="m-0 text-[13px] text-muted">还没有读到账号状态。</p>
    </section>

    <!-- 访问令牌 -->
    <section class="panel rounded-panel px-5 py-5">
      <header class="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div class="min-w-0">
          <h2 class="m-0 flex items-center gap-2 text-[14.5px] font-semibold text-ink">
            <PhKey :size="16" class="text-faint" aria-hidden="true" />
            数据源访问令牌
          </h2>
          <p class="m-0 mt-1 max-w-[76ch] text-xs leading-relaxed text-muted">
            本页的登录态、以及「公众号管理」的订阅列表，都走 we-mp-rss 的管理接口，需要 Bearer
            令牌。<strong class="text-ink">文章抓取本身不需要它</strong>（走公开的
            <code>/feed/*.json</code>）。
          </p>
        </div>
        <AppTag :tone="tokenMeta.tone" variant="outline">{{ tokenMeta.label }}</AppTag>
      </header>

      <dl class="mb-4 grid gap-4 sm:grid-cols-2">
        <div class="flex flex-col gap-1">
          <dt class="text-[12.5px] text-faint">令牌有效期至</dt>
          <dd class="m-0 nums font-mono text-[13.5px] text-ink">{{ tokenExpireLabel }}</dd>
        </div>
        <div class="flex flex-col gap-1">
          <dt class="text-[12.5px] text-faint">剩余时间</dt>
          <dd
            class="m-0 nums font-mono text-[13.5px]"
            :class="tokenExpired ? 'text-danger' : 'text-ink'"
          >
            {{ tokenCountdownLabel }}
          </dd>
        </div>
      </dl>

      <p class="m-0 mb-2 text-[13px] leading-relaxed text-muted">
        取一个令牌（账号密码见 <code>deploy/we-mp-rss/.env</code>）：
      </p>
      <div class="flex items-start gap-3 rounded-panel border border-hairline bg-inset px-3 py-2.5">
        <pre
          class="m-0 min-w-0 flex-1 overflow-auto font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap break-all text-ink"
        >{{ tokenCommand }}</pre>
        <AppCopyButton
          :value="tokenCommand"
          label="复制命令"
          success-message="命令已复制，凭据见 deploy/we-mp-rss/.env"
        />
      </div>

      <p class="m-0 mt-3 text-xs leading-relaxed text-muted">
        把输出粘到「配置管理 → 文章数据源 → 访问令牌」并保存。有效期由 we-mp-rss 的
        <code>token_expire_minutes</code> 决定（本部署配方是 3 天），到期后本页会提示。
      </p>
    </section>
  </div>
</template>
