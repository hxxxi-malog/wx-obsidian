<script setup lang="ts">
/**
 * 账号管理：微信读书登录态 + 数据源访问令牌。
 *
 * 四条语义必须严格保留（写错会直接误导用户）：
 *  1. **三态机**：`queried=false` 是「查不到」，不是「未登录」——
 *     未配置令牌时上游只会 401，把它显示成「未登录」会让人白跑一趟扫码；
 *  2. 上游的 `is_logged_in` 只说明「存了 Cookie」，不说明 Cookie 还能用：
 *     所以这里另外调用探针真实校验，凭据失效时才引导扫码重新登录；
 *  3. 凭据由 we-mp-rss 保存，本页负责校验与扫码登录，不接管凭据；
 *  4. 扫码成功后后端会自动逐个触发公众号采集，本页只轮询进度并展示。
 */
import {
  PhArrowClockwise,
  PhArrowsClockwise,
  PhKey,
  PhQrCode,
  PhShieldCheck,
  PhSignIn,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, onScopeDispose, ref } from 'vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppCopyButton from '@/components/ui/AppCopyButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import AppTag from '@/components/ui/AppTag.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import { ApiError, apiGet, apiPost, errorMessage } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type {
  AccountProbe,
  AccountStatus,
  QrLoginImage,
  QrLoginOver,
  QrLoginStatus,
} from '@/lib/api/types'
import { formatCountdown, formatDateTime } from '@/lib/format'
import type { Tone } from '@/lib/status'
import { useFeedSyncStore } from '@/stores/feed-sync'

const toast = useToast()
const feedSync = useFeedSyncStore()

/** 二维码状态 2 秒轮询一次（与后端契约一致） */
const QR_STATUS_INTERVAL_MS = 2000

type ProbeTrigger = 'initial' | 'manual' | 'silent'

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
  { immediate: false },
)

const account = computed(() => state.data.value)
const tokenConfigured = computed(() => account.value?.token?.configured ?? false)
const tokenExpired = computed(() => account.value?.token?.expired ?? false)

/** 登录态三态：查不到（neutral）≠ 未登录（danger） */
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

// -- 凭据探针 --------------------------------------------------------------

const PROBE_META: Record<AccountProbe['state'], { label: string; tone: Tone }> = {
  valid: { label: '有效', tone: 'success' },
  expired: { label: '已失效', tone: 'danger' },
  token_invalid: { label: '令牌无效', tone: 'warning' },
  unreachable: { label: '数据源不可达', tone: 'warning' },
  unknown: { label: '未知', tone: 'neutral' },
}

const probing = ref(false)
const refreshing = ref(false)
const opening = ref(false)
const probeResult = ref<AccountProbe | null>(null)

const probeMeta = computed(() => {
  const value = probeResult.value
  return value ? PROBE_META[value.state] : null
})

const probeCheckedLabel = computed(() => {
  const value = probeResult.value
  return value?.checked_at ? formatDateTime(value.checked_at) : '未知'
})

/** 每次进入页面最多自动弹一次扫码对话框：用户关掉后不再自动弹 */
let autoQrShown = false
let disposed = false

// -- 扫码登录对话框 --------------------------------------------------------

const qrOpen = ref(false)
const qrLoading = ref(false)
const qrImage = ref('')
const qrError = ref('')
const qrMessage = ref('')
const qrExpired = ref(false)
/** 扫了码但 Cookie 校验失败，或收尾未确认登录：错误块用「重新获取二维码」 */
const qrInvalid = ref(false)
const overLoading = ref(false)

let statusTimer: number | null = null
let statusBusy = false

/**
 * 取一个访问令牌的一句话命令。
 * 刻意不放凭据：账号密码在 deploy/we-mp-rss/.env 里，界面只给占位符。
 */
const tokenCommand = `curl -s -X POST http://localhost:8001/api/v1/wx/auth/token \\
  -d "username=<WERSS_USER>&password=<WERSS_PASS>" \\
  | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])"`

async function bootstrap(): Promise<void> {
  await state.run()
  await runProbe('initial')
}

async function refreshAll(): Promise<void> {
  if (refreshing.value) {
    return
  }
  refreshing.value = true
  try {
    await state.run()
    await runProbe('manual')
  } finally {
    refreshing.value = false
  }
}

async function runProbe(trigger: ProbeTrigger): Promise<void> {
  probing.value = true
  try {
    const result = await apiPost<AccountProbe>(endpoints.source.probe())
    if (disposed) {
      return
    }
    probeResult.value = result
    applyProbe(result, trigger)
  } catch (err) {
    if (disposed) {
      return
    }
    if (trigger !== 'silent') {
      toast.error(err, '凭据校验失败')
    }
  } finally {
    probing.value = false
  }
}

function applyProbe(result: AccountProbe, trigger: ProbeTrigger): void {
  // 只有 expired 才弹二维码：令牌问题与数据源不可达弹窗只会误导。
  // 登录收尾时的静默校验不弹窗——对话框正开着，弹了会打断结果展示。
  if (result.state === 'expired') {
    if (trigger === 'silent') {
      return
    }
    if (trigger === 'initial' && autoQrShown) {
      toast.warn('微信读书凭据已失效，可点「扫码重新登录」', result.message)
      return
    }
    autoQrShown = true
    void openQrDialog()
    return
  }
  if (trigger === 'silent') {
    return
  }
  switch (result.state) {
    case 'valid':
      if (trigger === 'manual') {
        toast.success(
          '微信读书凭据有效',
          result.feed_name ? `探测公众号：${result.feed_name}` : result.message,
        )
      }
      break
    case 'token_invalid':
      toast.warn('访问令牌无效，请到「配置管理 → 文章数据源」重新填写访问令牌', result.message)
      break
    case 'unreachable':
      toast.warn('数据源不可达，请确认 we-mp-rss 已启动', result.message)
      break
    default:
      toast.info('凭据状态未知', result.message)
  }
}

// -- 扫码登录 --------------------------------------------------------------

function clearTimers(): void {
  if (statusTimer !== null) {
    window.clearInterval(statusTimer)
    statusTimer = null
  }
}

function resetQrState(): void {
  clearTimers()
  statusBusy = false
  qrLoading.value = false
  qrImage.value = ''
  qrError.value = ''
  qrMessage.value = ''
  qrExpired.value = false
  qrInvalid.value = false
  overLoading.value = false
}

async function openQrDialog(): Promise<void> {
  resetQrState()
  qrOpen.value = true
  await loadQr()
}

function closeQrDialog(): void {
  qrOpen.value = false
  clearTimers()
}

function onQrOpenChange(open: boolean): void {
  if (open) {
    void openQrDialog()
    return
  }
  closeQrDialog()
}

async function loadQr(): Promise<void> {
  clearTimers()
  qrLoading.value = true
  qrImage.value = ''
  qrError.value = ''
  qrMessage.value = ''
  qrExpired.value = false
  qrInvalid.value = false
  try {
    const data = await apiGet<QrLoginImage>(endpoints.source.loginQr())
    if (disposed || !qrOpen.value) {
      return
    }
    // 失败也是 200：{error, detail}，没有 image 就按失败处理
    if (!data.image) {
      qrError.value = data.detail || data.error || '数据源没有返回二维码'
      return
    }
    qrImage.value = data.image
    startStatusPoll()
  } catch (err) {
    if (disposed) {
      return
    }
    qrError.value = errorMessage(err)
  } finally {
    qrLoading.value = false
  }
}

function startStatusPoll(): void {
  stopStatusPoll()
  statusTimer = window.setInterval(() => {
    void pollQrStatus()
  }, QR_STATUS_INTERVAL_MS)
}

function stopStatusPoll(): void {
  if (statusTimer !== null) {
    window.clearInterval(statusTimer)
    statusTimer = null
  }
}

async function pollQrStatus(): Promise<void> {
  if (statusBusy || !qrOpen.value) {
    return
  }
  statusBusy = true
  try {
    const status = await apiGet<QrLoginStatus>(endpoints.source.loginQrStatus())
    if (disposed || !qrOpen.value) {
      return
    }
    qrMessage.value = status.message
    qrExpired.value = status.message.includes('已过期')
    // 扫了码但 Cookie 校验失败：停轮询，让用户重新获取二维码
    if (status.logic_code === 'COOKIE_INVALID') {
      stopStatusPoll()
      qrInvalid.value = true
      qrError.value = status.message || '扫码完成，但微信读书 Cookie 校验失败'
      return
    }
    if (status.login_status) {
      stopStatusPoll()
      await finishLogin()
    }
  } catch (err) {
    if (disposed) {
      return
    }
    stopStatusPoll()
    qrError.value = errorMessage(err)
  } finally {
    statusBusy = false
  }
}

/**
 * 扫码成功后立刻关掉对话框，把后台更新交给右下角的进度卡。
 *
 * 后面的采集是后端线程在跑，用户不需要等：关掉窗口就能去「公众号管理」
 * 刷新列表，进度卡会自己走完并收起。
 */
async function finishLogin(): Promise<void> {
  overLoading.value = true
  try {
    const over = await apiPost<QrLoginOver>(endpoints.source.loginQrOver())
    if (disposed) {
      return
    }
    if (!over.login_status) {
      // 收尾没确认登录：留在二维码界面让用户重扫
      qrInvalid.value = true
      qrError.value = over.message || '扫码登录未完成，请重新获取二维码'
      return
    }

    closeQrDialog()
    if (over.sync_started) {
      feedSync.start()
      toast.success('扫码登录成功', '正在后台触发公众号更新，可继续使用其他页面')
    } else {
      toast.success('扫码登录成功', over.message || '微信读书凭据已恢复')
    }

    // 账号快照与凭据校验在后台刷新，不挡对话框关闭
    void (async () => {
      await state.run()
      await runProbe('silent')
      if (disposed) {
        return
      }
      if (probeResult.value?.state === 'valid') {
        toast.success('微信读书凭据已恢复', '稍等片刻后到「公众号管理」刷新即可看到最新时间')
      } else {
        toast.warn(
          '登录已完成，但凭据校验仍未通过',
          probeResult.value?.message || '请稍后点「刷新状态」重新校验',
        )
      }
    })()
  } catch (err) {
    if (disposed) {
      return
    }
    qrInvalid.value = true
    qrError.value = errorMessage(err)
  } finally {
    overLoading.value = false
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

onMounted(() => {
  void bootstrap()
})

onScopeDispose(() => {
  disposed = true
  clearTimers()
})
</script>

<template>
  <div>
    <PageHeader
      title="账号管理"
      description="微信读书凭据由 we-mp-rss 保存；这里可以校验凭据是否仍然有效，失效时扫码重新登录。"
    >
      <template #actions>
        <AppButton
          variant="outline"
          size="sm"
          :loading="state.loading.value || probing || refreshing"
          @click="refreshAll"
        >
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
      @retry="bootstrap"
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
            登录态由 we-mp-rss 保存（微信读书 Cookie）。Cookie 过期后公众号文章不再更新，可在这里扫码重新登录。
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
      </template>

      <p v-else class="m-0 text-[13px] text-muted">还没有读到账号状态。</p>

      <!-- 真实探针结果：「存了 Cookie」与「Cookie 还能用」不是一回事 -->
      <div class="mt-4 rounded-panel border border-hairline bg-inset px-3.5 py-3">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span class="text-[12.5px] text-faint">凭据校验</span>
          <AppTag v-if="probeMeta" :tone="probeMeta.tone" variant="outline">
            {{ probeMeta.label }}
          </AppTag>
          <AppTag v-else tone="neutral" variant="outline">
            {{ probing ? '校验中…' : '未校验' }}
          </AppTag>
          <span v-if="probeResult?.feed_name" class="text-xs text-muted">
            探测公众号：{{ probeResult.feed_name }}
          </span>
          <span v-if="probeResult" class="ml-auto text-xs text-faint">
            最近校验 {{ probeCheckedLabel }}
          </span>
        </div>
        <p v-if="probeResult?.message" class="m-0 mt-2 text-xs leading-relaxed text-muted">
          {{ probeResult.message }}
        </p>
      </div>

      <!-- 扫码登录任何时候都能点：账号快照读不到时也可能只是令牌问题 -->
      <div class="mt-5 flex flex-wrap items-center gap-2">
        <AppButton variant="solid" :loading="refreshing" @click="refreshAll">
          <template #icon>
            <PhArrowsClockwise :size="14" aria-hidden="true" />
          </template>
          重新读取状态
        </AppButton>
        <AppButton variant="tonal" @click="openQrDialog">
          <template #icon>
            <PhQrCode :size="14" aria-hidden="true" />
          </template>
          扫码重新登录
        </AppButton>
        <AppButton variant="outline" :loading="opening" @click="openLogin">
          <template #icon>
            <PhSignIn :size="14" aria-hidden="true" />
          </template>
          打开授权页面
        </AppButton>
      </div>

      <p class="m-0 mt-4 border-t border-hairline pt-4 text-xs leading-relaxed text-muted">
        「重新读取状态」会重新读取账号快照，并真实校验一次微信读书凭据；凭据失效时直接在本页扫码重新登录即可。
        扫码成功后窗口会自动关闭，公众号更新在后台进行，进度显示在右下角，期间可以正常使用其他页面。
        「打开授权页面」是兜底入口，打开的是 we-mp-rss 自带后台。
      </p>
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

    <!-- 扫码重新登录：只负责扫码；登录后的公众号更新在右下角进度卡里跑，不挡操作 -->
    <AppDialog
      :open="qrOpen"
      title="扫码重新登录微信读书"
      description="凭据由 we-mp-rss 保存；登录成功后会在后台触发公众号更新。"
      width="md"
      @update:open="onQrOpenChange"
    >
      <AppErrorState
        v-if="qrError"
        :message="qrError"
        hint="若二维码已过期请重新获取；扫码后仍失败可改用「打开授权页面」兜底。"
        :retry-label="qrInvalid ? '重新获取二维码' : '重试'"
        @retry="loadQr"
      />

      <div v-else-if="qrLoading" class="flex flex-col items-center gap-3 py-8">
        <AppProgress indeterminate size="sm" class="w-40" />
        <p class="m-0 text-[13px] text-muted">正在向数据源获取二维码…</p>
      </div>

      <div v-else-if="qrImage" class="flex flex-col items-center gap-3">
        <div class="rounded-panel border border-hairline bg-inset p-3">
          <img :src="qrImage" alt="微信读书登录二维码" class="block h-56 w-56" />
        </div>
        <p class="m-0 min-h-[20px] text-center text-[13px] leading-relaxed text-muted">
          {{ qrMessage || '请用微信扫描二维码完成登录' }}
        </p>
        <AppButton v-if="qrExpired" size="sm" variant="outline" :loading="qrLoading" @click="loadQr">
          <template #icon>
            <PhArrowsClockwise :size="14" aria-hidden="true" />
          </template>
          刷新二维码
        </AppButton>
      </div>

      <p v-else class="m-0 text-[13px] text-muted">还没有拿到二维码。</p>

      <template #footer>
        <p v-if="overLoading" class="m-0 mr-auto text-xs text-muted">正在确认登录状态…</p>
        <AppButton variant="ghost" :disabled="overLoading" @click="closeQrDialog">
          关闭
        </AppButton>
      </template>
    </AppDialog>
  </div>
</template>
