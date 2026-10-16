<script setup lang="ts">
/**
 * 配置管理：左侧分区导航 + 右侧表单（分区外观统一走 ConfigSection）。
 *
 * 三条不能丢的语义：
 *  1. 密钥留空 = 不修改（请求体由 store 的 buildConfigPayload 拼，页面不碰 patch/env 键名）；
 *  2. 数据源地址改动要重启服务、Web 端口改动要重启应用——提示必须在场；
 *  3. `?first_run=1` 深链（启动器首启跳转）落页即展开三步引导，保存成功后收起。
 */
import {
  PhArrowClockwise,
  PhBrain,
  PhEye,
  PhFolderOpen,
  PhGlobe,
  PhInfo,
  PhPlugs,
  PhPower,
  PhRocketLaunch,
  PhRss,
  PhX,
} from '@phosphor-icons/vue'
import { useEventListener } from '@vueuse/core'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import DirPicker from '@/components/DirPicker.vue'
import ConfigSection, { type ConfigSectionResult } from '@/components/domain/ConfigSection.vue'
import SaveBar from '@/components/domain/SaveBar.vue'
import SecretField from '@/components/domain/SecretField.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCombobox from '@/components/ui/AppCombobox.vue'
import AppCopyButton from '@/components/ui/AppCopyButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppField from '@/components/ui/AppField.vue'
import AppIconButton from '@/components/ui/AppIconButton.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppNumberInput from '@/components/ui/AppNumberInput.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import AppTag from '@/components/ui/AppTag.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useDirtyGuard } from '@/composables/useDirtyGuard'
import { useToast } from '@/composables/useToast'
import { ApiError, apiPost, hasCode } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { ConnectionTestTarget, FsPickResult } from '@/lib/api/types'
import { formFromSnapshot, useConfigStore } from '@/stores/config'
import type { ConfigForm } from '@/stores/config'

const route = useRoute()
const router = useRouter()
const config = useConfigStore()
const toast = useToast()

/** 启动器首启时直接落到 /settings?first_run=1 */
const firstRun = ref(route.query.first_run === '1')

const pickerVisible = ref(false)
const picking = ref(false)
const quitOpen = ref(false)
const quitting = ref(false)

/** 面板文案里的日志位置（服务没有终端，排错只能看这里） */
const logHint = '~/Library/Logs/wx-obsidian/app.log'

const SECTIONS = [
  { key: 'llm', label: '语言模型', icon: PhBrain },
  { key: 'vision', label: '视觉模型', icon: PhEye },
  { key: 'source', label: '文章数据源', icon: PhRss },
  { key: 'vault', label: '知识库与抓取', icon: PhFolderOpen },
  { key: 'web', label: 'Web 服务', icon: PhGlobe },
]

const activeSection = ref<string>(SECTIONS[0].key)

/**
 * 脏值守卫：直接传 reactive 对象即可（`useDirtyGuard` 内部靠 `JSON.stringify` 逐字段读取
 * 建立依赖，字段级改动会触发重算）。**不要**在里面做 `toRaw`，那样会断掉依赖追踪。
 */
const { dirty, markClean } = useDirtyGuard(() => config.form)

/** 「N 项未保存」：与「服务端快照还原出来的表单」逐字段比对，不是数输入事件 */
const changedCount = computed(() => {
  const snapshot = config.snapshot
  if (!snapshot) {
    return 0
  }
  const baseline = formFromSnapshot(snapshot)
  const current = config.form
  return (Object.keys(baseline) as (keyof ConfigForm)[]).filter(
    (key) => current[key] !== baseline[key],
  ).length
})

const llmOptions = computed(() =>
  (config.snapshot?.llm.available ?? []).map((name) => ({ label: name, value: name })),
)
const visionOptions = computed(() =>
  (config.snapshot?.vision.available ?? []).map((name) => ({ label: name, value: name })),
)

// -- 校验：只做「能立刻说清对错」的本地校验，服务端仍是最终裁判 -----------------

const URL_PATTERN = /^https?:\/\/.+/i

function urlError(value: string): string {
  const text = value.trim()
  if (!text) {
    return ''
  }
  return URL_PATTERN.test(text) ? '' : '地址需以 http:// 或 https:// 开头'
}

const llmActiveError = computed(() =>
  config.form.llmActive.trim() ? '' : '渠道名不能为空（留空则不会保存本分区的渠道设置）',
)
const visionActiveError = computed(() =>
  config.form.visionActive.trim() ? '' : '渠道名不能为空（留空则不会保存本分区的渠道设置）',
)
const llmUrlError = computed(() => urlError(config.form.llmBaseUrl))
const visionUrlError = computed(() => urlError(config.form.visionBaseUrl))
const sourceUrlError = computed(() => urlError(config.form.sourceBaseUrl))
const portError = computed(() =>
  config.form.webPort >= 1024 && config.form.webPort <= 65535 ? '' : '端口需在 1024–65535 之间',
)
const daysError = computed(() =>
  config.form.maxDays >= 1 && config.form.maxDays <= 365 ? '' : '天数需在 1–365 之间',
)
const workersError = computed(() =>
  config.form.maxWorkers >= 1 && config.form.maxWorkers <= 32 ? '' : '并行数需在 1–32 之间',
)

const hasErrors = computed(() =>
  [
    llmActiveError.value,
    visionActiveError.value,
    llmUrlError.value,
    visionUrlError.value,
    sourceUrlError.value,
    portError.value,
    daysError.value,
    workersError.value,
  ].some(Boolean),
)

/** 连通性结果按分区取（store 里按 llm / vision / source 三个键存最近一次结果） */
function resultsFor(keys: string[]): ConfigSectionResult[] {
  const out: ConfigSectionResult[] = []
  keys.forEach((key) => {
    const result = config.testResults[key]
    if (result) {
      out.push({ key, result })
    }
  })
  return out
}

// -- 动作 ---------------------------------------------------------------------

async function reload(): Promise<void> {
  await config.load()
  markClean()
  syncActiveSection()
}

async function save(): Promise<void> {
  if (hasErrors.value) {
    toast.warn('表单里还有校验错误', '修正后再保存；已保存的密钥不会因此丢失。')
    return
  }
  const result = await config.save()
  if (!result) {
    toast.error(config.error || '保存失败')
    return
  }
  toast.success(
    `已保存 ${result.applied.length} 项`,
    result.skipped.length ? `跳过 ${result.skipped.length} 项（留空的密钥不修改）` : undefined,
  )
  if (result.restart_required) {
    toast.warn('数据源地址改动需重启服务后生效')
  }
  markClean()
  if (firstRun.value) {
    dismissFirstRun()
  }
}

async function discard(): Promise<void> {
  await reload()
  toast.info('已放弃改动，重新载入服务端配置')
}

async function runTest(target: ConnectionTestTarget): Promise<void> {
  const result = await config.test(target)
  if (!result) {
    toast.error(config.error || '连通性测试失败')
    return
  }
  const failed = Object.entries(result).filter(([, value]) => !value.success)
  if (failed.length) {
    toast.warn('连通性测试有失败项', failed.map(([name, value]) => `${name}: ${value.message}`).join('；'))
  } else {
    toast.success('连通性正常')
  }
}

/** 原生目录选择面板：同时是解决 macOS TCC 权限的正确姿势。 */
async function pickNative(): Promise<void> {
  picking.value = true
  try {
    const result = await apiPost<FsPickResult>(endpoints.fs.pick(), { prompt: '选择 Obsidian Vault' })
    if (result.cancelled) {
      return
    }
    config.form.vaultPath = result.path
    toast.success('已选中，记得点「保存配置」')
  } catch (err) {
    if (hasCode(err, 'picker_unavailable') || (err instanceof ApiError && err.status === 501)) {
      toast.warn('当前启动方式不支持原生面板', '请改用旁边的「浏览…」在页面里选目录。')
    } else {
      toast.error(err)
    }
  } finally {
    picking.value = false
  }
}

function onPicked(path: string): void {
  config.form.vaultPath = path
  toast.info('已选中目录，记得点「保存配置」')
}

/** 退出应用：容器 / 后台运行下没有别的优雅停法，这是界面上的退出入口。 */
async function quitApp(): Promise<void> {
  quitting.value = true
  try {
    await apiPost(endpoints.shutdown())
    toast.success('应用已退出，可以关闭此页面了')
  } catch (err) {
    if (hasCode(err, 'not_supported') || (err instanceof ApiError && err.status === 501)) {
      toast.warn('当前启动方式不支持从页面退出', '请在启动它的终端里按 Ctrl+C 停止。')
    } else {
      toast.error(err)
    }
  } finally {
    quitting.value = false
    quitOpen.value = false
  }
}

function dismissFirstRun(): void {
  firstRun.value = false
  void router.replace({ path: '/settings' })
}

// -- 分区导航：点击滚动 + 滚动时反查当前分区 ------------------------------------

function scrollToSection(key: string): void {
  activeSection.value = key
  document
    .getElementById(`config-section-${key}`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function syncActiveSection(): void {
  let currentKey: string = SECTIONS[0].key
  for (const section of SECTIONS) {
    const element = document.getElementById(`config-section-${section.key}`)
    if (element && element.getBoundingClientRect().top <= 140) {
      currentKey = section.key
    }
  }
  activeSection.value = currentKey
}

useEventListener(window, 'scroll', syncActiveSection, { passive: true })

onMounted(reload)
</script>

<template>
  <div>
    <PageHeader
      title="配置管理"
      description="模型渠道、数据源、知识库与 Web 服务。改动要点「保存配置」才生效。"
    >
      <template #actions>
        <AppButton variant="outline" size="sm" :loading="config.loading" @click="reload">
          <template #icon>
            <PhArrowClockwise :size="14" aria-hidden="true" />
          </template>
          重新载入
        </AppButton>
        <AppButton variant="tonal" size="sm" :loading="config.testing.all" @click="runTest('all')">
          <template #icon>
            <PhPlugs :size="14" aria-hidden="true" />
          </template>
          全部测试
        </AppButton>
      </template>
    </PageHeader>

    <section v-if="firstRun" class="panel mb-5 rounded-panel px-5 py-4">
      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="m-0 flex items-center gap-2 text-[14px] font-medium text-ink">
            <PhRocketLaunch :size="16" class="text-accent" aria-hidden="true" />
            首次运行：三步就能跑起来
          </p>
          <ol class="mt-2 mb-0 flex list-decimal flex-col gap-1 pl-5 text-[13px] leading-relaxed text-muted">
            <li>填 <strong class="text-ink">API Key</strong>（语言模型那一栏，留空等于没配）</li>
            <li>
              选 <strong class="text-ink">Vault 路径</strong>——点「原生面板」在系统弹窗里挑，顺带完成 macOS 授权
            </li>
            <li>回「文章抓取」跑一轮；本页改动都要先点 <strong class="text-ink">保存配置</strong></li>
          </ol>
        </div>
        <AppIconButton label="关闭首启引导" size="sm" @click="dismissFirstRun">
          <PhX :size="15" aria-hidden="true" />
        </AppIconButton>
      </div>
    </section>

    <div class="mb-5 flex items-start gap-2 rounded-panel border border-hairline bg-inset px-3.5 py-3">
      <PhInfo :size="15" class="mt-0.5 shrink-0 text-faint" aria-hidden="true" />
      <p class="m-0 text-[12.5px] leading-relaxed text-muted">
        密钥留空表示不修改；已保存的 Key 只会以掩码形式回显。
      </p>
    </div>

    <AppErrorState
      v-if="config.error"
      class="mb-5"
      :message="config.error"
      hint="读不到配置时，下面的表单可能不是最新的服务端状态。"
      @retry="reload"
    />

    <div v-if="config.loading && !config.snapshot" class="flex flex-col gap-5">
      <AppSkeleton variant="block" height="196px" />
      <AppSkeleton variant="block" height="240px" />
    </div>

    <div v-else class="grid gap-6 lg:grid-cols-[208px_minmax(0,1fr)]">
      <!-- 移动端：横向滚动的分段控件（自身滚动，不把页面撑宽） -->
      <nav
        class="flex gap-1 overflow-x-auto pb-1 lg:sticky lg:top-[68px] lg:flex-col lg:gap-0.5 lg:self-start lg:overflow-visible lg:pb-0"
        aria-label="配置分区"
      >
        <button
          v-for="section in SECTIONS"
          :key="section.key"
          type="button"
          class="flex shrink-0 items-center gap-2 rounded-control px-3 py-2 text-left text-[13px] whitespace-nowrap transition-colors duration-150"
          :class="
            activeSection === section.key
              ? 'bg-raised text-ink'
              : 'text-muted hover:bg-raised/60 hover:text-ink'
          "
          :aria-current="activeSection === section.key ? 'true' : undefined"
          @click="scrollToSection(section.key)"
        >
          <component :is="section.icon" :size="15" class="shrink-0" aria-hidden="true" />
          {{ section.label }}
        </button>
      </nav>

      <!-- 宽屏下把表单列收窄：输入框拉满 1400px 会失去「精密仪表」的密度感 -->
      <div class="flex min-w-0 flex-col gap-5 xl:max-w-[920px]">
        <!-- 语言模型 -->
        <ConfigSection
          anchor="llm"
          title="语言模型"
          description="生成笔记用的模型渠道。渠道名可自由输入，保存时会写入该渠道的 Base URL 与模型。"
          :results="resultsFor(['llm'])"
        >
          <template #actions>
            <AppTag v-if="config.snapshot" variant="outline" mono>
              当前生效：{{ config.snapshot.llm.effective.model || '—' }}
            </AppTag>
            <AppButton size="sm" variant="outline" :loading="config.testing.llm" @click="runTest('llm')">
              测试连通性
            </AppButton>
          </template>

          <AppField label="生效渠道" required :error="llmActiveError">
            <template #default="{ id, invalid }">
              <AppCombobox
                :id="id"
                v-model="config.form.llmActive"
                :options="llmOptions"
                :invalid="invalid"
                allow-create
                placeholder="选择或输入渠道名"
              />
            </template>
          </AppField>

          <SecretField
            v-model="config.form.llmKey"
            label="API Key"
            :masked="config.snapshot?.llm.api_key ?? ''"
            hint="留空则不修改；环境变量 DEEPSEEK_API_KEY 会被写入 .env"
          />

          <AppField label="Base URL" :error="llmUrlError" hint="例如 https://api.deepseek.com">
            <template #default="{ id, invalid }">
              <AppInput
                :id="id"
                v-model="config.form.llmBaseUrl"
                mono
                :invalid="invalid"
                placeholder="https://…"
              />
            </template>
          </AppField>

          <AppField label="模型">
            <template #default="{ id }">
              <AppInput :id="id" v-model="config.form.llmModel" mono placeholder="deepseek-chat" />
            </template>
          </AppField>
        </ConfigSection>

        <!-- 视觉模型 -->
        <ConfigSection
          anchor="vision"
          title="视觉模型"
          description="文章配图的语义匹配走这个渠道；关闭后图片只做基础处理。"
          :results="resultsFor(['vision'])"
        >
          <template #actions>
            <AppTag v-if="config.snapshot" variant="outline" mono>
              当前生效：{{ config.snapshot.vision.effective.model || '—' }}
            </AppTag>
            <AppButton
              size="sm"
              variant="outline"
              :loading="config.testing.vision"
              @click="runTest('vision')"
            >
              测试连通性
            </AppButton>
          </template>

          <AppField label="启用视觉模型">
            <div class="flex items-center gap-3">
              <AppSwitch v-model="config.form.visionEnabled" label="启用视觉模型" />
              <span class="text-[12.5px] text-muted">
                {{ config.form.visionEnabled ? '已启用' : '已关闭（不调用视觉模型）' }}
              </span>
            </div>
          </AppField>

          <AppField label="生效渠道" required :error="visionActiveError">
            <template #default="{ id, invalid }">
              <AppCombobox
                :id="id"
                v-model="config.form.visionActive"
                :options="visionOptions"
                :invalid="invalid"
                allow-create
                placeholder="选择或输入渠道名"
              />
            </template>
          </AppField>

          <SecretField
            v-model="config.form.visionKey"
            label="API Key"
            :masked="config.snapshot?.vision.api_key ?? ''"
            hint="留空则不修改；未填时回落到渠道声明的环境变量"
          />

          <AppField label="Base URL" :error="visionUrlError" hint="例如 https://api.deepseek.com">
            <template #default="{ id, invalid }">
              <AppInput
                :id="id"
                v-model="config.form.visionBaseUrl"
                mono
                :invalid="invalid"
                placeholder="https://…"
              />
            </template>
          </AppField>

          <AppField label="模型">
            <template #default="{ id }">
              <AppInput :id="id" v-model="config.form.visionModel" mono placeholder="deepseek-chat" />
            </template>
          </AppField>
        </ConfigSection>

        <!-- 文章数据源 -->
        <ConfigSection
          anchor="source"
          title="文章数据源"
          description="按文章链接抓取的来源服务（当前配方是 we-mp-rss）。"
          :results="resultsFor(['source'])"
        >
          <template #actions>
            <AppButton
              size="sm"
              variant="outline"
              :loading="config.testing.source"
              @click="runTest('source')"
            >
              测试连通性
            </AppButton>
          </template>

          <AppField label="类型" hint="数据源实现名（we-mp-rss / rss）">
            <template #default="{ id }">
              <AppInput :id="id" v-model="config.form.sourceType" mono placeholder="we-mp-rss" />
            </template>
          </AppField>

          <AppField
            label="服务地址"
            :error="sourceUrlError"
            hint="数据源地址改动需重启服务后生效"
          >
            <template #default="{ id, invalid }">
              <AppInput
                :id="id"
                v-model="config.form.sourceBaseUrl"
                mono
                :invalid="invalid"
                placeholder="http://localhost:8001"
              />
            </template>
          </AppField>

          <SecretField
            v-model="config.form.sourceToken"
            label="访问令牌"
            :masked="config.snapshot?.source.token ?? ''"
            hint="留空则不修改；登录态与订阅列表都要它，文章抓取不需要"
          />
        </ConfigSection>

        <!-- 知识库与抓取 -->
        <ConfigSection
          anchor="vault"
          title="知识库与抓取"
          description="笔记写到哪个 Vault，以及一轮抓取的范围与并发。"
        >
          <AppField
            label="Vault 路径"
            hint="建议用「原生面板」在系统弹窗里选，顺带完成 macOS 的目录授权"
          >
            <template #default="{ id }">
              <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
                <!-- AppInput 是 inheritAttrs:false 且会丢掉 class，宽度只能由外层容器给 -->
                <div class="min-w-0 sm:flex-1">
                  <AppInput
                    :id="id"
                    v-model="config.form.vaultPath"
                    mono
                    clearable
                    placeholder="/Users/you/Documents/ObsidianVault"
                  />
                </div>
                <div class="flex items-center gap-2">
                  <AppButton
                    variant="outline"
                    :loading="picking"
                    @click="pickNative"
                  >
                    <template #icon>
                      <PhFolderOpen :size="14" aria-hidden="true" />
                    </template>
                    原生面板
                  </AppButton>
                  <AppButton variant="ghost" @click="pickerVisible = true">浏览…</AppButton>
                </div>
              </div>
            </template>
          </AppField>

          <AppField label="文章目录" hint="Vault 内的相对目录名，由后端配置决定，这里只读">
            <template #default="{ id }">
              <div class="flex items-center gap-2">
                <div class="min-w-0 flex-1">
                  <AppInput
                    :id="id"
                    :model-value="config.snapshot?.obsidian.articles_dir ?? ''"
                    readonly
                    mono
                  />
                </div>
                <AppCopyButton
                  v-if="config.snapshot?.obsidian.articles_dir"
                  :value="config.snapshot.obsidian.articles_dir"
                  label="复制"
                />
              </div>
            </template>
          </AppField>

          <div class="grid gap-5 sm:grid-cols-2">
            <AppField label="最大抓取天数" :error="daysError" hint="只抓这个天数窗口内的文章">
              <template #default="{ id, invalid }">
                <AppNumberInput
                  :id="id"
                  v-model="config.form.maxDays"
                  :min="1"
                  :max="365"
                  :presets="[1, 3, 7, 30]"
                  suffix="天"
                  :invalid="invalid"
                />
              </template>
            </AppField>

            <AppField label="最大并行数" :error="workersError" hint="同时处理几篇文章">
              <template #default="{ id, invalid }">
                <AppNumberInput
                  :id="id"
                  v-model="config.form.maxWorkers"
                  :min="1"
                  :max="32"
                  :presets="[1, 3, 5, 10]"
                  suffix="并发"
                  :invalid="invalid"
                />
              </template>
            </AppField>
          </div>
        </ConfigSection>

        <!-- Web 服务 -->
        <ConfigSection
          anchor="web"
          title="Web 服务"
          description="本页所在的本地服务；端口与自动开浏览器在下次启动时生效。"
        >
          <div class="grid gap-5 sm:grid-cols-2">
            <AppField label="端口" :error="portError" hint="改动需重启应用后生效">
              <template #default="{ id, invalid }">
                <AppNumberInput
                  :id="id"
                  v-model="config.form.webPort"
                  :min="1024"
                  :max="65535"
                  :invalid="invalid"
                />
              </template>
            </AppField>

            <AppField label="自动开浏览器" hint="启动服务后自动打开本页面">
              <div class="flex h-9 items-center gap-3">
                <AppSwitch v-model="config.form.webOpenBrowser" label="自动开浏览器" />
                <span class="text-[12.5px] text-muted">
                  {{ config.form.webOpenBrowser ? '已开启' : '已关闭' }}
                </span>
              </div>
            </AppField>
          </div>

          <AppField label="日志文件" hint="服务没有终端，排错只能看这个文件">
            <div class="flex items-center gap-2">
              <p class="m-0 min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink" :title="logHint">
                {{ logHint }}
              </p>
              <AppCopyButton :value="logHint" label="复制路径" />
            </div>
          </AppField>

          <div class="flex flex-col gap-2 border-t border-hairline pt-5">
            <div class="flex flex-wrap items-center gap-3">
              <AppButton variant="danger" :loading="quitting" @click="quitOpen = true">
                <template #icon>
                  <PhPower :size="14" aria-hidden="true" />
                </template>
                退出应用
              </AppButton>
              <p class="m-0 text-[12.5px] leading-relaxed text-muted">
                容器 / 后台运行下没有终端，停服务请用这个按钮（否则只能 docker stop 硬杀）。
              </p>
            </div>
          </div>
        </ConfigSection>
      </div>
    </div>

    <SaveBar
      :dirty="dirty"
      :count="changedCount"
      :saving="config.saving"
      :loading="config.loading"
      @save="save"
      @discard="discard"
    />

    <DirPicker v-model="pickerVisible" :start-path="config.form.vaultPath" @picked="onPicked" />

    <AppDialog
      :open="quitOpen"
      title="退出应用"
      description="退出后本页面将无法再连接服务，需要重新启动应用才能继续使用。"
      width="sm"
      @update:open="quitOpen = $event"
    >
      <p class="m-0 text-[13px] leading-relaxed text-muted">
        抓取中的任务会随服务一起中断。若当前启动方式不支持从页面退出，会如实提示。
      </p>
      <template #footer>
        <AppButton variant="ghost" @click="quitOpen = false">取消</AppButton>
        <AppButton variant="danger" :loading="quitting" @click="quitApp">确认退出</AppButton>
      </template>
    </AppDialog>
  </div>
</template>
