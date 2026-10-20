import { inRunOrder } from './diagnostic'
import type { QueryView } from '../api/types'

/** 当前筛选结果的统计：纠错后成功条数和平均耗时（只算有耗时的记录）。 */
export function logStats(items: Pick<QueryView, 'retry_count' | 'status' | 'duration_ms'>[]): { corrected: number; avgMs: number | null } {
  const timed = items.filter((q) => q.duration_ms > 0)
  return {
    corrected: items.filter((q) => q.status === 'success' && q.retry_count > 0).length,
    avgMs: timed.length ? Math.round(timed.reduce((s, q) => s + q.duration_ms, 0) / timed.length) : null,
  }
}

/** CSV 单元格：含逗号、引号、换行时加引号；以 = + - @ 开头的文本前面补单引号，防止表格软件当公式执行。 */
export function csvCell(v: unknown): string {
  let s = v == null ? '' : String(v)
  if (/^[=+\-@]/.test(s) && typeof v === 'string') s = `'${s}`
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export function toCsv(header: string[], rows: unknown[][]): string {
  return [header, ...rows].map((r) => r.map(csvCell).join(',')).join('\r\n')
}

/** 各环节耗时条：按开始顺序，宽度占最长环节的比例。 */
export function stageBars(timings: Record<string, number> | undefined): { name: string; ms: number; pct: number }[] {
  const entries = inRunOrder(Object.keys(timings ?? {})).map((name) => [name, timings?.[name] ?? 0] as const)
  const max = Math.max(0, ...entries.map(([, ms]) => ms))
  return entries.map(([name, ms]) => ({ name, ms, pct: max ? Math.max(2, Math.round((ms / max) * 100)) : 0 }))
}
