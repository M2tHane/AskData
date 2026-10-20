import type { FeedbackView } from '../api/types'

export const REASON: Record<string, string> = {
  metric_wrong: '口径不对',
  table_wrong: '选错表',
  column_wrong: '选错字段',
  empty_result: '结果为空',
  result_wrong: '结果不对',
  chart_wrong: '图表不对',
  other: '其他',
}

export const reasonLabel = (code: string | null | undefined) => (code ? (REASON[code] ?? code) : '')

/** 差评里出现最多的原因；没有带原因的差评时返回 null。并列时取先出现的。 */
export function topReason(items: Pick<FeedbackView, 'rating' | 'reason_code'>[]): { code: string; count: number } | null {
  const counts = new Map<string, number>()
  for (const f of items) if (f.rating === 'down' && f.reason_code) counts.set(f.reason_code, (counts.get(f.reason_code) ?? 0) + 1)
  let best: { code: string; count: number } | null = null
  for (const [code, count] of counts) if (!best || count > best.count) best = { code, count }
  return best
}

/** 差评率 = 近期差评数 / 近期查询总数；没有查询时无法计算，返回 null。 */
export function badRate(downs: number, queries: number): number | null {
  return queries > 0 ? downs / queries : null
}

export const isEvalConverted = (f: Pick<FeedbackView, 'converted_ref'>) => !!f.converted_ref?.startsWith('eval:')

/** 转评测题用的期望 SQL：来自当时的快照。 */
export function snapshotSql(f: Pick<FeedbackView, 'snapshot'>): string {
  const s = f.snapshot as Record<string, unknown>
  return typeof s.normalized_sql === 'string' ? s.normalized_sql : typeof s.generated_sql === 'string' ? s.generated_sql : ''
}
