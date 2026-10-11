<script setup lang="ts">
/**
 * 组件健康行（数据源 / LLM / Vision / Vault）。
 *
 * 三态由 `result` 是否存在决定，而不是靠 message 文本：
 *  - 未配置（null）：后端根本没探测，显示「未配置」+ 中性标签；
 *  - 正常 / 异常：探测结果，延迟一律等宽（ms 数字不对齐就没法纵向扫读）。
 *
 * 行与行之间只用一条发丝线分隔（由本组件的 `border-b` 提供），不套卡片。
 */
import { computed } from 'vue'

import AppSkeleton from '@/components/ui/AppSkeleton.vue'
import AppTag from '@/components/ui/AppTag.vue'
import type { ConnectionTestResult } from '@/lib/api/types'
import { formatLatency } from '@/lib/format'
import { connectionTone, type Tone } from '@/lib/status'

const props = withDefaults(
  defineProps<{
    label: string
    /** null = 未配置（后端返回 null，不是探测失败） */
    result?: ConnectionTestResult | null
    /** 首屏加载中：骨架与真实行同形 */
    loading?: boolean
    /** 延迟微条的参照值（同组内最大延迟，毫秒）；0 = 不画微条 */
    latencyMax?: number
  }>(),
  { result: null, loading: false, latencyMax: 0 },
)

const configured = computed(() => props.result !== null && props.result !== undefined)
const succeeded = computed(() => props.result?.success === true)

const tone = computed<Tone>(() => connectionTone(props.result?.success, configured.value))
const statusLabel = computed(() => {
  if (!configured.value) {
    return '未配置'
  }
  return succeeded.value ? '正常' : '异常'
})

const message = computed(() => {
  if (!configured.value) {
    return '尚未配置'
  }
  const text = props.result?.message?.trim()
  if (text) {
    return text
  }
  return succeeded.value ? '连通正常（后端未返回说明）' : '探测失败（后端未返回说明）'
})

const latency = computed(() => (configured.value ? formatLatency(props.result?.latency_ms) : '—'))

/** 微条只作「相对快慢」的视觉提示，数值本身仍是唯一权威 */
const barPercent = computed(() => {
  if (!configured.value || props.latencyMax <= 0) {
    return 0
  }
  const value = props.result?.latency_ms ?? 0
  return Math.max(4, Math.min(100, Math.round((value / props.latencyMax) * 100)))
})
</script>

<template>
  <div class="flex items-start gap-3 border-b border-hairline py-3 last:border-b-0">
    <template v-if="loading">
      <div class="min-w-0 flex-1">
        <AppSkeleton width="104px" height="12px" />
        <div class="mt-2"><AppSkeleton width="72%" height="10px" /></div>
      </div>
      <div class="flex shrink-0 items-center gap-3">
        <AppSkeleton width="46px" height="10px" />
        <AppSkeleton width="44px" height="20px" />
      </div>
    </template>

    <template v-else>
      <div class="min-w-0 flex-1">
        <p class="m-0 text-[13px] font-medium text-ink">{{ label }}</p>
        <p
          class="m-0 mt-0.5 text-[12px] leading-relaxed break-words"
          :class="configured ? 'text-muted' : 'text-faint'"
        >
          {{ message }}
        </p>
      </div>

      <div class="flex shrink-0 items-center gap-3 pt-0.5">
        <span class="w-[52px] text-right font-mono nums text-[11.5px] text-faint">
          {{ latency }}
        </span>

        <span
          v-if="configured && latencyMax > 0"
          class="hidden h-[3px] w-[44px] overflow-hidden rounded-full bg-inset sm:block"
        >
          <span
            class="block h-full rounded-full transition-[width] duration-500 ease-out-expo"
            :class="succeeded ? 'bg-success/70' : 'bg-danger/70'"
            :style="{ width: `${barPercent}%` }"
          />
        </span>

        <AppTag :tone="tone" variant="soft">{{ statusLabel }}</AppTag>
      </div>
    </template>
  </div>
</template>
