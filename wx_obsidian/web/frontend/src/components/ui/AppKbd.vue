<script setup lang="ts">
/** 键位提示（⌘K 这类），等宽 + 键帽造型。 */
withDefaults(defineProps<{ keys?: string }>(), { keys: '' })
</script>

<template>
  <kbd
    class="inline-flex h-[20px] items-center gap-1 rounded-[5px] border border-hairline bg-raised px-1.5 font-mono text-[10.5px] leading-none text-faint"
  >
    <template v-if="keys">{{ keys }}</template>
    <slot v-else />
  </kbd>
</template>
