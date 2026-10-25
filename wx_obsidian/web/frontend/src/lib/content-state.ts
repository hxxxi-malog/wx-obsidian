/**
 * 正文状态 → 标签文案与重试入口（纯函数，便于单测）。
 *
 * 「无正文」已拆成三种真实状态：补抓中 / 待补抓 / 补抓失败。补抓中已经在跑，
 * 重复触发只会被上游跳过；待补抓与补抓失败才需要用户点「重试」。
 */

import type { ContentState } from '@/lib/api/types'
import type { Tone } from '@/lib/status'

export interface ContentStateMeta {
  label: string
  tone: Tone
  variant: 'soft' | 'outline'
}

/** 无正文的三种状态各自的标签；`ready` 不显示标签，故不在此表 */
export const CONTENT_STATE_META: Record<Exclude<ContentState, 'ready'>, ContentStateMeta> = {
  fetching: { label: '补抓中', tone: 'info', variant: 'soft' },
  pending: { label: '待补抓', tone: 'warning', variant: 'outline' },
  failed: { label: '补抓失败', tone: 'danger', variant: 'outline' },
}

/** 取状态标签；`ready` 表示已有正文，不显示任何标签 */
export function contentStateMeta(state: ContentState): ContentStateMeta | null {
  return state === 'ready' ? null : CONTENT_STATE_META[state]
}

/** 只有待补抓与补抓失败需要给「重试」按钮 */
export function needsContentRetry(state: ContentState): boolean {
  return state === 'pending' || state === 'failed'
}

/** 补抓进度百分比：按「已触发 / 本轮开始时待补抓」估计 */
export function backfillPercent(pending: number, refreshed: number): number {
  if (pending <= 0) {
    return 0
  }
  return Math.max(0, Math.min(100, Math.round((refreshed / pending) * 100)))
}
