/**
 * 「最新文章」页的日期分组：纯函数，不依赖 Vue 与接口，便于单测。
 *
 * 分组按**浏览器本地时区**计算：上游给的是带时区的 ISO 时间，用户看到的
 * 「今天/昨天」必须与本地日历一致，否则凌晨发布的文章会被分到前一天。
 */

import type { SourceArticleItem } from '@/lib/api/types'

const WEEKDAY_LABELS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'] as const

/** 发布时间缺失的分组：固定排在所有日期分组之后 */
export const UNKNOWN_DAY_KEY = 'unknown'
export const UNKNOWN_DAY_LABEL = '时间未知'

export interface ArticleDayGroup {
  /** 本地日期 `2026-10-23`；时间未知的分组固定为 `unknown` */
  key: string
  /** 分组标题：今天 / 昨天 / 10月20日 周二 / 时间未知 */
  label: string
  /** 组内按发布时间倒序 */
  items: SourceArticleItem[]
}

/** 解析发布时间；空值或非法字符串返回 null（由调用方归入「时间未知」） */
export function parsePublishedAt(raw: string | null | undefined): Date | null {
  if (!raw) {
    return null
  }
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 本地日期键 `2026-10-23`：补零后可直接按字符串比较大小 */
export function dayKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

/** 分组标题：今天 / 昨天 / 10月20日 周二；跨年时补上年份 */
export function dayLabel(date: Date, now: Date = new Date()): string {
  const key = dayKey(date)
  if (key === dayKey(now)) {
    return '今天'
  }
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  if (key === dayKey(yesterday)) {
    return '昨天'
  }
  const text = `${date.getMonth() + 1}月${date.getDate()}日 ${WEEKDAY_LABELS[date.getDay()]}`
  return date.getFullYear() === now.getFullYear() ? text : `${date.getFullYear()}年${text}`
}

/** 按本地日期把文章分组：日期倒序，组内发布时间倒序，时间未知的组排最后 */
export function groupByPublishedDay(
  items: readonly SourceArticleItem[],
  now: Date = new Date(),
): ArticleDayGroup[] {
  const sorted = [...items].sort(compareByPublishedAtDesc)
  const groups = new Map<string, ArticleDayGroup>()
  const unknownItems: SourceArticleItem[] = []

  sorted.forEach((item) => {
    const date = parsePublishedAt(item.published_at)
    if (!date) {
      unknownItems.push(item)
      return
    }
    const key = dayKey(date)
    const group = groups.get(key)
    if (group) {
      group.items.push(item)
      return
    }
    groups.set(key, { key, label: dayLabel(date, now), items: [item] })
  })

  const result = [...groups.values()]
  if (unknownItems.length > 0) {
    result.push({ key: UNKNOWN_DAY_KEY, label: UNKNOWN_DAY_LABEL, items: unknownItems })
  }
  return result
}

/** 发布时间倒序；两条都没有时间时视为相等，保持原有相对次序（sort 是稳定排序） */
function compareByPublishedAtDesc(left: SourceArticleItem, right: SourceArticleItem): number {
  const leftTime = parsePublishedAt(left.published_at)?.getTime() ?? Number.NEGATIVE_INFINITY
  const rightTime = parsePublishedAt(right.published_at)?.getTime() ?? Number.NEGATIVE_INFINITY
  if (leftTime === rightTime) {
    return 0
  }
  return rightTime - leftTime
}
