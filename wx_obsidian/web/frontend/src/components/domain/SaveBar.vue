<script setup lang="ts">
/**
 * 保存条：粘在视口底部的操作条（glass + 上边线），显示未保存项数。
 *
 * 为什么粘底而不是页尾：配置页很长，改动散落在 5 个分区里；保存按钮滚出视口后
 * 用户只能往上找，很容易以为「改完就生效了」。脏值判断在页面里（useDirtyGuard），
 * 这里只负责呈现与转发动作。
 */
import { PhArrowCounterClockwise, PhFloppyDisk } from '@phosphor-icons/vue'
import { computed } from 'vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppStatusDot from '@/components/ui/AppStatusDot.vue'

const props = withDefaults(
  defineProps<{
    dirty: boolean
    /** 未保存的字段数（与后端基线逐字段比对得出） */
    count?: number
    saving?: boolean
    /** 首次载入中：此时按钮不可点，避免拿空表单覆盖服务端配置 */
    loading?: boolean
    saveLabel?: string
    discardLabel?: string
  }>(),
  {
    count: 0,
    saving: false,
    loading: false,
    saveLabel: '保存配置',
    discardLabel: '放弃改动',
  },
)

const emit = defineEmits<{
  (event: 'save'): void
  (event: 'discard'): void
}>()

const busy = computed(() => props.saving || props.loading)

const statusText = computed(() => {
  if (props.saving) {
    return '正在保存…'
  }
  if (props.loading) {
    return '正在载入服务端配置…'
  }
  if (!props.dirty) {
    return '所有改动已保存'
  }
  return props.count > 0 ? `${props.count} 项未保存` : '有未保存的改动'
})
</script>

<template>
  <div class="glass sticky bottom-0 z-20 -mx-4 mt-6 border-t border-hairline px-4 py-3 sm:-mx-6 sm:px-6">
    <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div class="flex items-center gap-2 text-[13px]">
        <AppStatusDot :tone="dirty ? 'warning' : 'success'" :pulse="dirty" />
        <span :class="dirty ? 'text-ink' : 'text-muted'">{{ statusText }}</span>
      </div>

      <div class="flex items-center gap-2">
        <AppButton
          variant="ghost"
          size="sm"
          :disabled="!dirty || busy"
          @click="emit('discard')"
        >
          <template #icon>
            <PhArrowCounterClockwise :size="14" aria-hidden="true" />
          </template>
          {{ discardLabel }}
        </AppButton>

        <AppButton
          variant="solid"
          size="sm"
          :loading="saving"
          :disabled="!dirty || loading"
          @click="emit('save')"
        >
          <template #icon>
            <PhFloppyDisk :size="14" aria-hidden="true" />
          </template>
          {{ saveLabel }}
        </AppButton>
      </div>
    </div>
  </div>
</template>
