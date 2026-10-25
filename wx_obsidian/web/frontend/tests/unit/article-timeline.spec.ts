import { describe, expect, it } from 'vitest'

import type { SourceArticleItem } from '@/lib/api/types'
import {
  UNKNOWN_DAY_KEY,
  UNKNOWN_DAY_LABEL,
  dayKey,
  dayLabel,
  groupByPublishedDay,
  parsePublishedAt,
} from '@/lib/article-timeline'

function article(id: string, published: Date | string | null): SourceArticleItem {
  return {
    article_id: id,
    upstream_id: `upstream-${id}`,
    title: `文章 ${id}`,
    url: 'https://mp.weixin.qq.com/s/example',
    account: '测试公众号',
    account_id: 'MP_TEST',
    published_at: published instanceof Date ? published.toISOString() : published,
    cover: '',
    has_content: true,
    content_state: 'ready',
    content_attempts: 0,
    processed: false,
  }
}

describe('parsePublishedAt', () => {
  it('空值与非法字符串返回 null', () => {
    expect(parsePublishedAt(null)).toBeNull()
    expect(parsePublishedAt(undefined)).toBeNull()
    expect(parsePublishedAt('')).toBeNull()
    expect(parsePublishedAt('看不清的时间')).toBeNull()
  })

  it('解析带时区的 ISO 时间', () => {
    expect(parsePublishedAt('2026-10-23T05:06:48+00:00')?.toISOString()).toBe(
      '2026-10-23T05:06:48.000Z',
    )
  })
})

describe('dayKey', () => {
  it('月份与日期补零，得到 YYYY-MM-DD', () => {
    expect(dayKey(new Date(2026, 0, 5, 9, 30))).toBe('2026-01-05')
    expect(dayKey(new Date(2026, 11, 31, 23, 59))).toBe('2026-12-31')
  })
})

describe('dayLabel', () => {
  // 2026-10-23 是周五
  const now = new Date(2026, 9, 23, 12, 0)

  it('今天与昨天按本地日期判断', () => {
    expect(dayLabel(new Date(2026, 9, 23, 0, 5), now)).toBe('今天')
    expect(dayLabel(new Date(2026, 9, 22, 23, 55), now)).toBe('昨天')
  })

  it('更早的日期给出月日与星期', () => {
    expect(dayLabel(new Date(2026, 9, 20, 8, 0), now)).toBe('10月20日 周二')
    expect(dayLabel(new Date(2026, 9, 17, 8, 0), now)).toBe('10月17日 周六')
  })

  it('跨年时补上年份', () => {
    expect(dayLabel(new Date(2025, 11, 31, 8, 0), now)).toBe('2025年12月31日 周三')
  })
})

describe('groupByPublishedDay', () => {
  const now = new Date(2026, 9, 23, 12, 0)

  it('空列表返回空数组', () => {
    expect(groupByPublishedDay([], now)).toEqual([])
  })

  it('按本地日期分组，日期倒序，组内发布时间倒序', () => {
    const groups = groupByPublishedDay(
      [
        article('昨天', new Date(2026, 9, 22, 9, 0)),
        article('今天早', new Date(2026, 9, 23, 8, 0)),
        article('今天晚', new Date(2026, 9, 23, 10, 0)),
        article('前天', new Date(2026, 9, 21, 8, 0)),
      ],
      now,
    )

    expect(groups.map((group) => group.key)).toEqual(['2026-10-23', '2026-10-22', '2026-10-21'])
    expect(groups.map((group) => group.label)).toEqual(['今天', '昨天', '10月21日 周三'])
    expect(groups.map((group) => group.items.map((item) => item.article_id))).toEqual([
      ['今天晚', '今天早'],
      ['昨天'],
      ['前天'],
    ])
  })

  it('时间未知的分组排在最后，并保留原有相对次序', () => {
    const groups = groupByPublishedDay(
      [
        article('未知一', null),
        article('今天', new Date(2026, 9, 23, 8, 0)),
        article('未知二', '看不清的时间'),
      ],
      now,
    )

    expect(groups).toHaveLength(2)
    const last = groups[groups.length - 1]
    expect(last.key).toBe(UNKNOWN_DAY_KEY)
    expect(last.label).toBe(UNKNOWN_DAY_LABEL)
    expect(last.items.map((item) => item.article_id)).toEqual(['未知一', '未知二'])
  })

  it('按本地日期切分：同一 UTC 日期的深夜与次日凌晨分属两组', () => {
    const groups = groupByPublishedDay(
      [
        article('深夜', new Date(2026, 9, 22, 23, 30)),
        article('凌晨', new Date(2026, 9, 23, 0, 10)),
      ],
      now,
    )

    expect(groups.map((group) => group.key)).toEqual(['2026-10-23', '2026-10-22'])
  })
})
