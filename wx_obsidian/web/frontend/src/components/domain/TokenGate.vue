<script setup lang="ts">
/**
 * 访问令牌闸门：没有 token / token 失效时的**可恢复**提示。
 *
 * 为什么需要它：token 只在启动器打开的 URL 里出现一次，前端读完立刻从地址栏抹掉
 * （避免进历史记录与 Referer）。于是「手动敲 localhost:8765」「点收藏夹」
 * 「新标签页」进来的标签页必然没有 token —— 以前能做的只有看一屏 401 报错。
 * 这里给出唯一正确的动作：粘贴 token → 服务端校验 → 整页刷新。
 *
 * 安全性没有放宽：token 依旧只存本标签页的 sessionStorage；服务端绝不把 token
 * 回传给未鉴权的请求；校验失败不写入存储，也不会覆盖本来能用的 token。
 */
import { computed, ref } from 'vue'

import AppButton from '@/components/ui/AppButton.vue'
import AppCodeBlock from '@/components/ui/AppCodeBlock.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppErrorState from '@/components/ui/AppErrorState.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import { errorMessage, normalizeTokenInput, verifyToken } from '@/lib/api/client'
import { useAuthStore } from '@/stores/auth'

/** token 的三个来源（顺序 = 推荐顺序：文件最直接，日志是兜底） */
const TOKEN_HELP = [
  '# 任意运行方式：启动器把 token 落在固定文件里（权限 0600）',
  'cat ~/.wx-obsidian/web.token',
  '',
  '# Docker 部署：容器日志里启动时会打印带 token 的完整地址',
  'docker compose logs wx-obsidian | grep 已启动',
].join('\n')

const auth = useAuthStore()

const candidate = ref('')
const error = ref('')
const checking = ref(false)

const bannerMessage = computed(() =>
  auth.status === 'rejected'
    ? '未授权：当前标签页的访问令牌已失效'
    : '未授权：当前标签页没有访问令牌（token）',
)
const bannerHint = computed(() =>
  auth.status === 'rejected'
    ? '服务重启后随机 token 可能已经变过。粘贴当前 token 即可恢复，不用重启应用。'
    : '直接打开地址（不带 ?token=）就会这样：页面能开，但接口全部拒绝。粘贴 token 即可恢复。',
)

const dialogOpen = computed({
  get: () => auth.dialogOpen,
  set: (value: boolean) => (value ? auth.openDialog() : auth.closeDialog()),
})

async function submit(): Promise<void> {
  const value = normalizeTokenInput(candidate.value)
  if (!value || checking.value) {
    return
  }
  checking.value = true
  error.value = ''
  try {
    const accepted = await verifyToken(value)
    if (!accepted) {
      error.value = '这个 token 不被服务接受：请把 ~/.wx-obsidian/web.token 里的完整内容粘贴进来。'
      return
    }
    auth.unlock(value)
    // 整页刷新最可靠：所有页面、轮询与 SSE 都从「无 token」的状态重新开始
    window.location.reload()
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    checking.value = false
  }
}
</script>

<template>
  <!-- 常驻横幅：不随页面滚动消失，任何页面都能一键打开解锁对话框 -->
  <div v-if="auth.needsToken" data-token-gate class="border-b border-hairline bg-page">
    <div class="mx-auto w-full max-w-[1400px] px-4 py-3 sm:px-6">
      <AppErrorState
        compact
        :message="bannerMessage"
        :hint="bannerHint"
        retry-label="输入 token"
        @retry="auth.openDialog()"
      />
    </div>
  </div>

  <AppDialog
    v-model:open="dialogOpen"
    title="需要访问令牌"
    description="这个服务能写你的硬盘、读你的 API Key，所以每个接口都要求 token。"
    width="md"
  >
    <div data-token-dialog class="flex flex-col gap-4">
      <AppField
        label="访问令牌（token）"
        hint="粘贴 token 本身，或直接粘贴启动器打印的整条地址；只写进本标签页的 sessionStorage。"
        :error="error"
      >
        <template #default="{ id, invalid }">
          <AppInput
            :id="id"
            v-model="candidate"
            data-token-input
            mono
            :invalid="invalid"
            placeholder="粘贴 token 或带 token 的完整地址"
            @enter="submit"
          />
        </template>
      </AppField>

      <div class="flex flex-col gap-2">
        <p class="m-0 text-[12.5px] font-medium text-muted">token 在哪</p>
        <AppCodeBlock :code="TOKEN_HELP" />
        <p class="m-0 text-xs leading-relaxed text-faint">
          容器部署用
          <code class="font-mono text-ink">WX_WEB_TOKEN</code>
          固定下来后，token 不会随重启变化，收藏夹里的地址也就能一直用。
        </p>
      </div>
    </div>

    <template #footer>
      <AppButton variant="ghost" @click="auth.closeDialog()">稍后</AppButton>
      <AppButton
        data-token-submit
        variant="solid"
        :loading="checking"
        :disabled="!candidate.trim()"
        @click="submit"
      >
        解锁
      </AppButton>
    </template>
  </AppDialog>
</template>
