import { describe, expect, it } from 'vitest'

import { VEIL_BANDS, VEIL_FADE_DISTANCE, veilProgressFor } from '@/lib/scroll'

describe('顶栏遮罩进度', () => {
  it('静止在顶部时不绘制', () => {
    expect(veilProgressFor(0)).toBe(0)
  })

  it('负位移与非法值按 0 处理（回弹、NaN 不会让遮罩乱闪）', () => {
    expect(veilProgressFor(-120)).toBe(0)
    expect(veilProgressFor(Number.NaN)).toBe(0)
    expect(veilProgressFor(Number.POSITIVE_INFINITY)).toBe(0)
  })

  it('按淡入距离线性推进并封顶', () => {
    expect(veilProgressFor(VEIL_FADE_DISTANCE / 2)).toBeCloseTo(0.5, 5)
    expect(veilProgressFor(VEIL_FADE_DISTANCE)).toBe(1)
    expect(veilProgressFor(4000)).toBe(1)
  })

  it('标题刚压到栏底时遮罩已接近满强度', () => {
    // 顶栏 56px + main 上内边距 24px = 标题在 y≈24 时开始被栏底切到
    expect(veilProgressFor(24)).toBeGreaterThan(0.7)
  })

  it('淡入距离可覆盖，非法距离退化为「立刻满强度」', () => {
    expect(veilProgressFor(50, 100)).toBeCloseTo(0.5, 5)
    expect(veilProgressFor(1, 0)).toBe(1)
    expect(veilProgressFor(1, Number.NaN)).toBe(1)
  })

  it('层数与 CSS 的 nth-child 规则数一致', () => {
    expect(VEIL_BANDS).toBe(6)
  })
})
