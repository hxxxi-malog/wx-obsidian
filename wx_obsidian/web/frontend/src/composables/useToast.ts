/** 统一提示（替代 Element Plus 的 ElMessage / ElMessageBox 的轻量场景）。 */

import { errorMessage } from '@/lib/api/client'
import { useUiStore } from '@/stores/ui'

export interface ToastAction {
  label: string
  run: () => void
}

export function useToast() {
  const ui = useUiStore()

  return {
    success(title: string, description?: string, action?: ToastAction): number {
      return ui.pushToast({ tone: 'success', title, description, action })
    },
    info(title: string, description?: string, action?: ToastAction): number {
      return ui.pushToast({ tone: 'info', title, description, action })
    },
    warn(title: string, description?: string, action?: ToastAction): number {
      return ui.pushToast({ tone: 'warning', title, description, action })
    },
    /** 错误统一入口：传 ApiError 时会自动套用 401 等既有文案 */
    error(err: unknown, title = '操作失败', action?: ToastAction): number {
      const description = typeof err === 'string' ? err : errorMessage(err)
      return ui.pushToast({ tone: 'danger', title, description, action, timeout: 6000 })
    },
    dismiss(id: number): void {
      ui.dismissToast(id)
    },
  }
}
