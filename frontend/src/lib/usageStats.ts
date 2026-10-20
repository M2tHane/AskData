import type { TrendPoint } from '../api/types'

/** 大数缩写，和图表坐标轴一致用「万」「亿」：8,320 原样，53,000 → 5.3万，2 亿 → 2亿。完整值请放在悬停提示里。 */
export function compactNumber(n: number): string {
  const sign = n < 0 ? '-' : ''
  const v = Math.abs(n)
  if (v < 1e4) return `${sign}${Math.round(v).toLocaleString('zh-CN')}`
  const short = (x: number) => x.toFixed(1).replace(/\.0$/, '')
  // 四舍五入后恰好到 1 万万时进一档，避免出现 10000万。
  if (v >= 1e8 || Number((v / 1e4).toFixed(1)) >= 1e4) return `${sign}${short(v / 1e8)}亿`
  return `${sign}${short(v / 1e4)}万`
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
