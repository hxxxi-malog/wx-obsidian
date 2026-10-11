<script setup lang="ts">
/**
 * 配置分区：一个分区 = 一个抬升面 + 发丝线标题栏 + 表单体。
 *
 * 为什么单独抽：
 *  1. 配置页有 5 个分区，标题栏结构（标题 / 说明 / 生效值 / 测试按钮 / 测试结果）
 *     必须完全一致——散在页面里写五遍必然漂移；
 *  2. 左侧分区导航靠 `anchor` 定位滚动，id 规则只在这里定义一次；
 *  3. 连通性结果「就地显示」在标题栏右侧：成功/失败 + 延迟，不弹全局提示。
 */
import { computed } from 'vue'

import AppPanel from '@/components/ui/AppPanel.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'
import type { ConnectionTestResult } from '@/lib/api/types'
import { formatLatency } from '@/lib/format'
import { connectionTone } from '@/lib/status'

export interface ConfigSectionResult {
  /** 结果标签（例如 llm / vision / source），用于区分多结果场景 */
  key: string
  result: ConnectionTestResult
}

const props = withDefaults(
  defineProps<{
    /** 分区锚点（左侧导航 scrollIntoView 的目标），会渲染成 config-section-<anchor> */
    anchor: string
    title: string
    description?: string
    /** 最近一次连通性测试结果（就地显示，含失败） */
    results?: ConfigSectionResult[]
  }>(),
  { description: '', results: () => [] },
)

const domId = computed(() => `config-section-${props.anchor}`)
</script>

<template>
  <AppPanel as="section" padding="none" :id="domId" class="scroll-mt-24">
    <header
      class="flex flex-wrap items-start justify-between gap-x-5 gap-y-3 border-b border-hairline px-5 py-4"
    >
      <div class="min-w-0">
        <h2 class="m-0 text-[14.5px] font-semibold text-ink">{{ title }}</h2>
        <p v-if="description" class="m-0 mt-1 max-w-[70ch] text-xs leading-relaxed text-muted">
          {{ description }}
        </p>
        <slot name="meta" />
      </div>

      <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
        <!-- 测试结果就地显示：点一次按钮就能看到「通不通 + 多久」，不用去翻全局提示 -->
        <span
          v-for="item in results"
          :key="item.key"
          class="inline-flex items-center gap-1.5 text-[11.5px] leading-none"
        >
          <AppStatusDot :tone="connectionTone(item.result.success)" :size="6" />
          <span :class="item.result.success ? 'text-success' : 'text-danger'">
            {{ item.result.success ? '连通' : '失败' }}
          </span>
          <span class="nums font-mono text-faint">{{ formatLatency(item.result.latency_ms) }}</span>
          <span class="max-w-[36ch] truncate text-faint" :title="item.result.message">
            {{ item.result.message }}
          </span>
        </span>

        <slot name="actions" />
      </div>
    </header>

    <div class="flex flex-col gap-5 px-5 py-5">
      <slot />
    </div>
  </AppPanel>
</template>
