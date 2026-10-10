import type { TrendPoint } from '../api/types'

/** 大数缩写：1,234 → 1.2K，12,400,000 → 12.4M；千以内原样。完整值请放在悬停提示里。 */
export function compactNumber(n: number): string {
  const units = ['', 'K', 'M', 'B']
  const sign = n < 0 ? '-' : ''
  let v = Math.abs(n)
  if (v < 1000) return `${sign}${Math.round(v).toLocaleString('zh-CN')}`
  let i = 0
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000
    i += 1
  }
  // 四舍五入后恰好到 1000 时进一档，避免出现 1000.0K。
  if (Number(v.toFixed(1)) >= 1000 && i < units.length - 1) {
    v /= 1000
    i += 1
  }
  return `${sign}${v.toFixed(1).replace(/\.0$/, '')}${units[i]}`
}

export interface PeriodTotals {
  calls: number
  tokens: number
  /** 平均每次调用的 Token；没有调用时为 0。 */
  perCall: number
}

/** 所选时间段内的合计。 */
export function periodTotals(points: readonly TrendPoint[]): PeriodTotals {
  let calls = 0
  let tokens = 0
  for (const p of points) {
    calls += p.calls
    tokens += p.tokens_in + p.tokens_out
  }
  return { calls, tokens, perCall: calls ? Math.round(tokens / calls) : 0 }
}
