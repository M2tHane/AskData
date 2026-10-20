import type { QuotaUsage, TrendPoint } from '../api/types'

export interface TrendTable {
  rows: Record<string, unknown>[]
  keys: string[]
}

/** 把「日期×对象」的长表变成图表用的宽表：每天一行、每个对象一列（Token 总量），最多 4 个对象，其余合并为「其他」。 */
export function pivotTrend(points: TrendPoint[], since: string, until: string, label: (key: string) => string, top = 4): TrendTable {
  const total = new Map<string, number>()
  for (const p of points) total.set(p.key, (total.get(p.key) ?? 0) + p.tokens_in + p.tokens_out)
  const ranked = [...total.entries()].sort((a, b) => b[1] - a[1]).map(([key]) => key)
  const shown = ranked.slice(0, top)
  const keys = ranked.length > top ? [...shown.map(label), '其他'] : shown.map(label)
  const days: string[] = []
  for (let t = Date.parse(since); t <= Date.parse(until); t += 86_400_000) days.push(new Date(t).toISOString().slice(0, 10))
  const rows = days.map((day) => {
    const row: Record<string, unknown> = { date: day.slice(5) }
    for (const k of keys) row[k] = 0
    return row
  })
  const byDay = new Map(days.map((d, i) => [d, rows[i] as Record<string, unknown>]))
  for (const p of points) {
    const row = byDay.get(p.date)
    if (!row) continue
    const name = shown.includes(p.key) ? label(p.key) : '其他'
    row[name] = Number(row[name] ?? 0) + p.tokens_in + p.tokens_out
  }
  return { rows, keys }
}

export const QUOTA_LABEL: Record<QuotaUsage['kind'], string> = { monthly_tokens: '本月 Token', daily_queries: '今日查询' }
export const LEVEL_LABEL: Record<QuotaUsage['level'], string> = { ok: '正常', warning: '接近上限', exceeded: '已超限' }

/** 需要提醒管理员的条目：接近或超过上限。 */
export const alertsOnly = (items: QuotaUsage[]) => items.filter((i) => i.level !== 'ok')
