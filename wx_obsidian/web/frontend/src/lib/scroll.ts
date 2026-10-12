/**
 * 滚动位移 → 遮罩进度的纯计算（见 styles/veil.css）。
 *
 * 单独成文件是为了能脱离浏览器单测：组件里只负责把 window.scrollY 接进来。
 */

/** 模糊层数：必须与 styles/veil.css 中 `.veil__blur > :nth-child(n)` 的规则数一致 */
export const VEIL_BANDS = 6

/**
 * 淡入距离（px）：滚过这段距离后遮罩完全不透明。
 * 取 32（约顶栏高度的一半）——标题从栏底露头时遮罩已经糊到 75% 以上，
 * 等到正文整体压到栏下时已经是满强度，中途不会出现「半透明硬切」。
 */
export const VEIL_FADE_DISTANCE = 32

/** 把滚动位移映射为 0→1 的遮罩进度；负值与非法值按 0 处理，超出封顶 1 */
export function veilProgressFor(scrollY: number, fadeDistance: number = VEIL_FADE_DISTANCE): number {
  if (!Number.isFinite(scrollY) || scrollY <= 0) {
    return 0
  }
  if (!Number.isFinite(fadeDistance) || fadeDistance <= 0) {
    return 1
  }
  return Math.min(1, scrollY / fadeDistance)
}
