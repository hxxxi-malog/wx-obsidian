/** 展示层格式化：时间、数字、时长、路径。所有页面共用，避免各写一份。 */

const DASH = '—'

function parse(raw: string | null | undefined): Date | null {
  if (!raw) {
    return null
  }
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 完整时间：2026/10/12 00:18:03（解析不了就原样返回，别吞掉后端给的值） */
export function formatDateTime(raw: string | null | undefined): string {
  if (!raw) {
    return DASH
  }
  const date = parse(raw)
  return date ? date.toLocaleString('zh-CN', { hour12: false }) : raw
}

/** 只要时分秒 */
export function formatTime(raw: string | null | undefined): string {
  if (!raw) {
    return DASH
  }
  const date = parse(raw)
  return date ? date.toLocaleTimeString('zh-CN', { hour12: false }) : raw
}

/** 相对时间：刚刚 / 12 分钟前 / 3 小时前 / 2 天前 / 绝对时间 */
export function formatRelative(raw: string | null | undefined, now: Date = new Date()): string {
  if (!raw) {
    return DASH
  }
  const date = parse(raw)
  if (!date) {
    return raw
  }
  const diff = now.getTime() - date.getTime()
  const future = diff < 0
  const seconds = Math.round(Math.abs(diff) / 1000)
  if (seconds < 45) {
    return future ? '马上' : '刚刚'
  }
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) {
    return future ? `${minutes} 分钟后` : `${minutes} 分钟前`
  }
  const hours = Math.round(minutes / 60)
  if (hours < 24) {
    return future ? `${hours} 小时后` : `${hours} 小时前`
  }
  const days = Math.round(hours / 24)
  if (days <= 7) {
    return future ? `${days} 天后` : `${days} 天前`
  }
  return formatDateTime(raw)
}

/** 倒计时（用于定时任务「下次触发」）：3 小时 12 分后 / 已过期 */
export function formatCountdown(raw: string | null | undefined, now: Date = new Date()): string {
  const date = parse(raw)
  if (!date) {
    return DASH
  }
  const diff = date.getTime() - now.getTime()
  if (diff <= 0) {
    return '已到期'
  }
  const totalMinutes = Math.floor(diff / 60000)
  const days = Math.floor(totalMinutes / 1440)
  const hours = Math.floor((totalMinutes % 1440) / 60)
  const minutes = totalMinutes % 60
  if (days > 0) {
    return `${days} 天 ${hours} 小时后`
  }
  if (hours > 0) {
    return `${hours} 小时 ${minutes} 分后`
  }
  if (minutes > 0) {
    return `${minutes} 分后`
  }
  return '不到 1 分钟'
}

/** 千分位 */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return DASH
  }
  return new Intl.NumberFormat('zh-CN').format(value)
}

/** 延迟：52 ms */
export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) {
    return DASH
  }
  return `${Math.round(ms)} ms`
}

/** 耗时：820 ms / 12.4 s / 3 分 05 秒 */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) {
    return DASH
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`
  }
  const seconds = ms / 1000
  if (seconds < 60) {
    return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`
  }
  const minutes = Math.floor(seconds / 60)
  const rest = Math.floor(seconds % 60)
  if (minutes < 60) {
    return `${minutes} 分 ${String(rest).padStart(2, '0')} 秒`
  }
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${minutes % 60} 分`
}

/** 任务已运行时长（起点是 ISO 时间；无终点则算到 now） */
export function formatElapsed(startISO: string | null | undefined, endISO?: string | null): string {
  const start = parse(startISO)
  if (!start) {
    return DASH
  }
  const end = endISO ? parse(endISO) : new Date()
  const stop = end ?? new Date()
  return formatDuration(stop.getTime() - start.getTime())
}

/** 路径缩写：只保留尾部，头用 … 省略（表格里给完整路径会把列撑爆） */
export function shortPath(path: string, max = 48): string {
  if (!path) {
    return DASH
  }
  if (path.length <= max) {
    return path
  }
  const keep = Math.max(8, max - 1)
  return `…${path.slice(path.length - keep)}`
}

/** 文本截断（标题两行展示时的兜底） */
export function truncate(text: string, max = 80): string {
  if (!text) {
    return ''
  }
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`
}

/** 文件名/标题的占位首字（公众号封面缺失时用） */
export function initialOf(text: string): string {
  const trimmed = (text ?? '').trim()
  if (!trimmed) {
    return '·'
  }
  const first = Array.from(trimmed)[0] ?? '·'
  return first.toUpperCase()
}

/** 由字符串稳定推导一个色相（占位色块用；同名永远同色） */
export function stableHue(text: string): number {
  let hash = 0
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) % 360
  }
  return hash
}
