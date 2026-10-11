<script setup lang="ts">
/**
 * 左侧导航：按用途分组，当前项用铜金指示条 + 抬升面（不是浅蓝底）。
 * 折叠态只留图标（都带 Tooltip）；移动端复用同一组件（放进抽屉）。
 */
import { PhSidebarSimple } from '@phosphor-icons/vue'
import { computed } from 'vue'

import AppTooltip from '@/components/ui/AppTooltip.vue'
import { NAV_GROUPS, SETTINGS_ITEM } from '@/lib/navigation'
import { useUiStore } from '@/stores/ui'

withDefaults(defineProps<{ variant?: 'rail' | 'drawer' }>(), { variant: 'rail' })

const emit = defineEmits<{ (event: 'navigate'): void }>()

const ui = useUiStore()
const isCollapsed = computed(() => ui.railCollapsed)
</script>

<template>
  <nav class="flex h-full flex-col gap-1 px-2.5 py-3" aria-label="主导航">
    <div class="mb-2 flex items-center gap-2 px-1.5 pt-1" :class="isCollapsed ? 'justify-center' : ''">
      <span
        class="grid size-7 shrink-0 place-items-center rounded-[8px] border border-hairline bg-raised font-mono text-[13px] font-semibold text-accent"
        aria-hidden="true"
      >
        W
      </span>
      <div v-if="!isCollapsed" class="min-w-0">
        <p class="m-0 truncate text-[13.5px] leading-tight font-semibold text-ink">wx-obsidian</p>
        <p class="m-0 truncate text-[11px] leading-tight text-faint">公众号 → 知识库</p>
      </div>
    </div>

    <div
      v-for="group in NAV_GROUPS"
      :key="group.label"
      class="flex flex-col gap-0.5"
      :class="isCollapsed ? 'mt-1' : 'mt-2'"
    >
      <p
        v-if="!isCollapsed"
        class="m-0 px-2.5 py-1 text-[11px] font-medium tracking-wide text-faint"
      >
        {{ group.label }}
      </p>
      <div v-else class="mx-2 my-1 border-t border-hairline" aria-hidden="true" />

      <AppTooltip
        v-for="item in group.items"
        :key="item.key"
        :content="item.description"
        side="right"
        :disabled="!isCollapsed"
      >
        <router-link
          :to="item.path"
          class="group relative flex items-center rounded-[9px] py-2 text-[13.5px] transition-colors duration-150"
          :class="[
            isCollapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5',
            $route.meta.navKey === item.key
              ? 'bg-raised font-medium text-ink'
              : 'text-muted hover:bg-raised/60 hover:text-ink',
          ]"
          @click="emit('navigate')"
        >
          <span
            v-if="$route.meta.navKey === item.key"
            class="absolute top-1/2 left-0 h-4 w-[2px] -translate-y-1/2 rounded-full bg-accent"
            aria-hidden="true"
          />
          <component :is="item.icon" :size="17" class="shrink-0" aria-hidden="true" />
          <span v-if="!isCollapsed" class="truncate">{{ item.label }}</span>
        </router-link>
      </AppTooltip>
    </div>

    <div class="mt-auto flex flex-col gap-1 pt-3">
      <div class="border-t border-hairline pt-2" aria-hidden="true" />

      <AppTooltip :content="SETTINGS_ITEM.description" side="right" :disabled="!isCollapsed">
        <router-link
          :to="SETTINGS_ITEM.path"
          class="relative flex items-center rounded-[9px] py-2 text-[13.5px] transition-colors duration-150"
          :class="[
            isCollapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5',
            $route.meta.navKey === SETTINGS_ITEM.key
              ? 'bg-raised font-medium text-ink'
              : 'text-muted hover:bg-raised/60 hover:text-ink',
          ]"
          @click="emit('navigate')"
        >
          <span
            v-if="$route.meta.navKey === SETTINGS_ITEM.key"
            class="absolute top-1/2 left-0 h-4 w-[2px] -translate-y-1/2 rounded-full bg-accent"
            aria-hidden="true"
          />
          <component :is="SETTINGS_ITEM.icon" :size="17" class="shrink-0" aria-hidden="true" />
          <span v-if="!isCollapsed" class="truncate">{{ SETTINGS_ITEM.label }}</span>
        </router-link>
      </AppTooltip>

      <button
        v-if="variant === 'rail'"
        type="button"
        class="hidden items-center rounded-[9px] py-2 text-[12.5px] text-faint transition-colors hover:bg-raised/60 hover:text-ink lg:flex"
        :class="isCollapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5'"
        :aria-label="isCollapsed ? '展开侧栏' : '收起侧栏'"
        @click="ui.toggleRail()"
      >
        <PhSidebarSimple :size="16" aria-hidden="true" />
        <span v-if="!isCollapsed">收起侧栏</span>
      </button>
    </div>
  </nav>
</template>
