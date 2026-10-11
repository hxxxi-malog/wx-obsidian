import { describe, expect, it } from 'vitest'

import {
  formatCountdown,
  formatDateTime,
  formatDuration,
  formatElapsed,
  formatNumber,
  formatRelative,
  formatTime,
  initialOf,
  shortPath,
  stableHue,
  truncate,
} from '@/lib/format'

const NOW = new Date('2026-10-12T12:00:00+08:00')

describe('时间格式化', () => {
  it('空值与非法值不吞掉原文', () => {
    expect(formatDateTime('')).toBe('—')
    expect(formatDateTime('not-a-date')).toBe('not-a-date')
    expect(formatTime(null)).toBe('—')
    expect(formatRelative(undefined)).toBe('—')
  })

  it('相对时间覆盖刚刚/分钟/小时/天', () => {
    expect(formatRelative('2026-10-12T11:59:40+08:00', NOW)).toBe('刚刚')
    expect(formatRelative('2026-10-12T11:30:00+08:00', NOW)).toBe('30 分钟前')
    expect(formatRelative('2026-10-12T08:00:00+08:00', NOW)).toBe('4 小时前')
    expect(formatRelative('2026-10-09T12:00:00+08:00', NOW)).toBe('3 天前')
  })

  it('未来时间说「后」而不是负数', () => {
    expect(formatRelative('2026-10-12T13:00:00+08:00', NOW)).toBe('1 小时后')
  })

  it('超过一周回落到绝对时间', () => {
    expect(formatRelative('2026-09-01T12:00:00+08:00', NOW)).toContain('2026')
  })

  it('倒计时按天/小时/分钟分层', () => {
    expect(formatCountdown('2026-10-14T12:00:00+08:00', NOW)).toBe('2 天 0 小时后')
    expect(formatCountdown('2026-10-12T15:30:00+08:00', NOW)).toBe('3 小时 30 分后')
    expect(formatCountdown('2026-10-12T12:20:00+08:00', NOW)).toBe('20 分后')
    expect(formatCountdown('2026-10-12T11:00:00+08:00', NOW)).toBe('已到期')
    expect(formatCountdown(null, NOW)).toBe('—')
  })

  it('耗时按量级换单位', () => {
    expect(formatDuration(0)).toBe('0 ms')
    expect(formatDuration(820)).toBe('820 ms')
    expect(formatDuration(4200)).toBe('4.2 s')
    expect(formatDuration(12400)).toBe('12 s')
    expect(formatDuration(185000)).toBe('3 分 05 秒')
    expect(formatDuration(3 * 3600 * 1000 + 25 * 60 * 1000)).toBe('3 小时 25 分')
    expect(formatDuration(-1)).toBe('—')
  })

  it('已用时长：结束时间缺失时算到现在', () => {
    const start = '2026-10-12T11:59:00+08:00'
    expect(formatElapsed(start, '2026-10-12T12:00:00+08:00')).toBe('1 分 00 秒')
    expect(formatElapsed(null)).toBe('—')
  })
})

describe('数字与文本', () => {
  it('千分位与空值', () => {
    expect(formatNumber(1234567)).toBe('1,234,567')
    expect(formatNumber(0)).toBe('0')
    expect(formatNumber(null)).toBe('—')
  })

  it('路径缩写保留尾部', () => {
    expect(shortPath('')).toBe('—')
    expect(shortPath('/a/b.md')).toBe('/a/b.md')
    const long = `/Users/me/Documents/公众号文章/AI产业生态/${'x'.repeat(60)}.md`
    const short = shortPath(long, 32)
    expect(short.startsWith('…')).toBe(true)
    expect(short.length).toBeLessThanOrEqual(32)
    expect(short.endsWith('.md')).toBe(true)
  })

  it('截断与首字', () => {
    expect(truncate('abcdef', 4)).toBe('abc…')
    expect(truncate('abc', 4)).toBe('abc')
    expect(initialOf('  大模型  ')).toBe('大')
    expect(initialOf('')).toBe('·')
    expect(initialOf('agent')).toBe('A')
  })

  it('色相稳定（同名同色）', () => {
    expect(stableHue('机器之心')).toBe(stableHue('机器之心'))
    expect(stableHue('机器之心')).toBeGreaterThanOrEqual(0)
    expect(stableHue('机器之心')).toBeLessThan(360)
  })
})
