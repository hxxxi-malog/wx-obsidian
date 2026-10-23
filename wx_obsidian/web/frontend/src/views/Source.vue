<script setup lang="ts">
/**
 * 数据源状态：探活结果做成页面主视觉，不可达时把「怎么把它跑起来」摆在最前面。
 *
 * 运维边界（这句不能丢）：本应用不代为管理容器，只做探活。
 */
import { PhArrowClockwise, PhHardDrives, PhWarningCircle } from '@phosphor-icons/vue'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppButton from '@/components/ui/AppButton.vue'
import AppCopyButton from '@/components/ui/AppCopyButton.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import AppTag from '@/components/ui/AppTag.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { useAsync } from '@/composables/useAsync'
import { apiGet } from '@/lib/api/client'
import { endpoints } from '@/lib/api/endpoints'
import type { SourceHealth } from '@/lib/api/types'
import { formatRelative } from '@/lib/format'

const router = useRouter()

/** 三步修复：命令原文一字不改，只是各配一句「这一步在干什么」 */
const DOCKER_STEPS = [
  { label: '进入配方目录', command: 'cd deploy/we-mp-rss' },
  { label: '准备凭据', command: 'cp .env.example .env   # 填 WERSS_PASS / WEREAD_COOKIE' },
  { label: '起容器', command: 'docker compose up -d --build' },
]

const allCommands = DOCKER_STEPS.map((step) => step.command).join('\n')

const probedAt = ref('')

const state = useAsync<SourceHealth>(
  async (signal) => {
    const data = await apiGet<SourceHealth>(endpoints.source.health(), signal)
    probedAt.value = new Date().toISOString()
    return data
  },
  { immediate: true },
)

const health = computed(() => state.data.value)
const probedLabel = computed(() => (probedAt.value ? formatRelative(probedAt.value) : '—'))
</script>

<template>
  <div>
    <PageHeader
      title="数据源状态"
      description="we-mp-rss 探活；它不可达时，抓取、订阅列表与登录态都会一起失效。"
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

    <AppErrorState
      v-if="state.error.value"
      class="mb-5"
      :message="state.error.value"
      hint="探活请求本身失败（例如未授权）：未授权时页面顶部会给出「输入 token」入口，粘贴后即可恢复。"
      @retry="state.run()"
    />

    <!-- 状态主视觉 -->
    <section class="panel mb-5 rounded-panel px-5 py-5">
      <div v-if="state.loading.value && !health" class="flex flex-col gap-3">
        <AppSkeleton variant="stat" />
        <AppSkeleton variant="line" width="46%" />
      </div>

      <div v-else-if="health" class="flex flex-wrap items-start justify-between gap-x-8 gap-y-4">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-3">
            <AppStatusDot
              :tone="health.healthy ? 'success' : 'danger'"
              :size="10"
              :pulse="health.healthy"
            />
            <p
              class="m-0 text-[26px] leading-none font-semibold tracking-tight"
              :class="health.healthy ? 'text-success' : 'text-danger'"
            >
              {{ health.healthy ? '运行中' : '不可达' }}
            </p>
            <AppTag :tone="health.healthy ? 'success' : 'danger'" variant="outline">
              {{ health.healthy ? 'healthy' : 'unreachable' }}
            </AppTag>
          </div>

          <dl class="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-[12.5px]">
            <div class="flex items-center gap-2">
              <dt class="text-faint">服务地址</dt>
              <dd class="m-0 flex items-center gap-1 font-mono text-ink">
                {{ health.base_url || '—' }}
                <AppCopyButton v-if="health.base_url" :value="health.base_url" label="复制" />
              </dd>
            </div>
            <div class="flex items-center gap-2">
              <dt class="text-faint">数据源类型</dt>
              <dd class="m-0 font-mono text-ink">{{ health.type || '—' }}</dd>
            </div>
            <div class="flex items-center gap-2">
              <dt class="text-faint">最后探测</dt>
              <dd class="m-0 nums font-mono text-muted">{{ probedLabel }}</dd>
            </div>
          </dl>
        </div>

        <div class="flex items-center gap-2">
          <AppCopyButton v-if="health.base_url" :value="health.base_url" label="复制地址" />
          <AppButton variant="outline" size="sm" :loading="state.loading.value" @click="state.run()">
            重新探测
          </AppButton>
        </div>
      </div>

      <p v-else class="m-0 text-[13px] text-muted">还没有探测结果。</p>
    </section>

    <!-- 不可达：置顶醒目提示，并把两条后续动作直接摆出来 -->
    <section
      v-if="health && !health.healthy"
      class="mb-5 flex flex-wrap items-start gap-3 rounded-panel border border-warning/30 bg-warning-soft px-4 py-3.5"
      role="alert"
    >
      <PhWarningCircle :size="18" class="mt-px shrink-0 text-warning" aria-hidden="true" />
      <div class="min-w-0 flex-1">
        <p class="m-0 text-[13.5px] leading-relaxed font-medium text-ink">
          服务不可达。抓取会失败，请先按下方的命令把数据源跑起来。
        </p>
        <p class="m-0 mt-1 text-xs leading-relaxed text-muted">
          容器不由本应用管理；起来之后回到本页点「刷新状态」确认。
        </p>
      </div>
      <div class="flex shrink-0 flex-wrap items-center gap-2">
        <AppButton size="sm" variant="outline" @click="router.push('/feeds')">公众号管理</AppButton>
        <AppButton size="sm" variant="outline" @click="router.push('/account')">账号管理</AppButton>
      </div>
    </section>

    <!-- 启动指引 -->
    <section class="panel rounded-panel px-5 py-5">
      <header class="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div class="min-w-0">
          <h2 class="m-0 flex items-center gap-2 text-[14.5px] font-semibold text-ink">
            <PhHardDrives :size="16" class="text-faint" aria-hidden="true" />
            启动数据源（Docker）
          </h2>
          <p class="m-0 mt-1 text-xs leading-relaxed text-muted">
            在仓库根目录按顺序执行；凭据只写在 deploy/we-mp-rss/.env 里，不进本页面。
          </p>
        </div>
        <AppCopyButton :value="allCommands" label="复制全部" />
      </header>

      <ol class="m-0 flex list-none flex-col gap-2 p-0">
        <li
          v-for="(step, index) in DOCKER_STEPS"
          :key="step.command"
          class="flex items-start gap-3 rounded-panel border border-hairline bg-inset px-3 py-2.5"
        >
          <span class="nums mt-0.5 font-mono text-[11.5px] text-faint">{{ index + 1 }}</span>
          <div class="min-w-0 flex-1">
            <p class="m-0 text-[11.5px] text-faint">{{ step.label }}</p>
            <!-- whitespace-pre-wrap：命令里的对齐空格要原样显示（复制的是原串）；
                 插值必须紧贴标签，否则模板缩进会被当成命令的一部分 -->
            <code
              class="font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap break-all text-ink"
            >{{ step.command }}</code>
          </div>
          <AppCopyButton :value="step.command" label="复制" />
        </li>
      </ol>

      <p class="m-0 mt-4 border-t border-hairline pt-4 text-xs leading-relaxed text-muted">
        启动后访问 <code>http://localhost:8001</code> 完成初始化并登录微信读书。本应用不代为管理容器，只做探活。
      </p>
    </section>
  </div>
</template>
