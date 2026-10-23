/**
 * 登录后「触发公众号更新」的视图模型（纯函数，便于单测）。
 *
 * 后端在扫码登录成功后自己起后台线程逐个触发采集，前端只轮询
 * `GET /api/source/sync` 并把进度画在右下角，不阻塞用户。
 */

import type { FeedSyncState } from '@/lib/api/types'

export interface FeedSyncView {
  percent: number
  running: boolean
  title: string
  detail: string
  tone: 'accent' | 'success' | 'warning'
}

const TAIL = '稍等片刻后到「公众号管理」刷新即可看到最新时间。'
const FREE_HINT = '可以继续使用其他页面。'

/** 已处理数：已触发 + 被跳过 + 失败（后端 total 是订阅总数） */
function processed(data: FeedSyncState): number {
  return data.triggered + data.skipped + data.failed
}

export function feedSyncPercent(data: FeedSyncState | null): number {
  if (!data || data.total <= 0) {
    return 0
  }
  return Math.max(0, Math.min(100, Math.round((processed(data) / data.total) * 100)))
}

export function feedSyncView(data: FeedSyncState | null, error = ''): FeedSyncView {
  if (!data) {
    return {
      percent: 0,
      running: true,
      title: '正在触发公众号更新…',
      detail: `${FREE_HINT}${TAIL}`,
      tone: 'accent',
    }
  }

  const percent = feedSyncPercent(data)

  if (data.running) {
    return {
      percent,
      running: true,
      title:
        data.total > 0
          ? `正在触发公众号更新 ${processed(data)}/${data.total}`
          : `正在触发公众号更新（已触发 ${data.triggered} 个）`,
      detail: `${FREE_HINT}${TAIL}`,
      tone: 'accent',
    }
  }

  const notes: string[] = []
  if (data.total === 0) {
    notes.push('当前没有订阅的公众号')
  } else {
    notes.push(`已触发 ${data.triggered} 个公众号更新`)
  }
  if (data.skipped > 0) {
    notes.push(`${data.skipped} 个因过于频繁被跳过`)
  }
  if (data.failed > 0) {
    notes.push(`${data.failed} 个触发失败`)
  }

  const failed = data.failed > 0 || Boolean(error)
  return {
    percent: failed ? percent : 100,
    running: false,
    title: failed ? '部分公众号更新触发失败' : '公众号更新已触发',
    detail: `${notes.join('，')}。${failed ? '可稍后重试。' : TAIL}${error ? `（${error}）` : ''}`,
    tone: failed ? 'warning' : 'success',
  }
}
