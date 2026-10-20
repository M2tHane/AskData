import type { Evaluation, RunListItem } from '../api/types'
import { friendlyError } from './errors'
import { formatInt, formatTime } from './format'

export const CATEGORY: Record<string, string> = {
  single_table: '单表查询',
  join: '多表关联',
  analytics: '分析计算',
  time: '时间口径',
  semantic: '语义与指标',
  chart: '图表',
  clarify: '澄清',
  violation: '越权访问',
  out_of_scope: '超出范围',
  injection: '提示注入',
  empty: '空结果',
  upload: '上传数据',
}

export const EXPECT: Record<string, string> = { success: '返回结果', blocked: '拦截', clarify: '先澄清' }
export const ACTUAL: Record<string, string> = { success: '返回了结果', blocked: '被拦截', clarify: '要求澄清', failed: '执行失败' }
export const DIFFICULTY: Record<string, string> = { easy: '简单', medium: '中等', hard: '困难' }
export const RUN_STATUS: Record<string, string> = { queued: '排队中', running: '运行中', success: '已完成', failed: '已中断', canceled: '已停止' }
export const isActive = (status: string) => status === 'queued' || status === 'running'
/** 停止或中断后还有没答的题，可以从断点接着跑。 */
export const canResume = (r: { status: string; done: number; case_count: number }) => (r.status === 'canceled' || r.status === 'failed') && r.done < r.case_count

/** 未通过的原因说成人话；带上实际发生了什么。 */
export function reasonText(r: Pick<Evaluation, 'reason' | 'actual_status' | 'blocked_code' | 'error'>): string {
  const actual = r.actual_status ? ACTUAL[r.actual_status] ?? r.actual_status : ''
  switch (r.reason) {
    case 'passed':
      return '通过'
    case 'result_mismatch':
      return '查询结果与标准答案不一致'
    case 'status_mismatch':
      return `应返回结果，实际${actual || '没有返回'}`
    case 'safety_mismatch':
      return r.actual_status === 'blocked' ? `拦截原因不符（${r.blocked_code ?? '未知'}）` : `应被拦截，实际${actual}`
    case 'clarification_mismatch':
      return r.actual_status === 'clarify' ? '澄清选项不包含期望的口径' : `应先澄清，实际${actual}`
    case 'unexpected_empty_reference':
      return '标准答案为空，题目或测试数据需要检查'
    case 'empty_explanation_missing':
      return '结果为空但没有给出说明'
    case 'chart_mismatch':
      return '图表类型不符合要求'
    case 'semantic_mismatch':
      return '没有用到期望的语义或指标'
    case 'time_range_mismatch':
      return '时间范围算得不对'
    case 'assumption_missing':
      return '直接回答了，但没有说明按哪种理解'
    case 'execution_failed':
      return `执行失败：${friendlyError(r.error ?? undefined).title}`
    default:
      return r.reason
  }
}

/** 所有题都在没有产生任何 Token 时执行失败，说明评测夹具（团队、用户、测试库）没有在这套环境里准备好。 */
export function looksUnprovisioned(rows: { reason: string; tokens_in: number; tokens_out: number }[]): boolean {
  return rows.length > 0 && rows.every((r) => r.reason === 'execution_failed' && r.tokens_in === 0 && r.tokens_out === 0)
}

export const pct = (v: unknown) => (typeof v === 'number' ? `${Math.round(v * 1000) / 10}%` : '—')
export const num = (v: unknown) => (typeof v === 'number' ? v : undefined)
export const passRate = (r: { done: number; passed: number }) => (r.done ? pct(r.passed / r.done) : '—')
export const runLabel = (r: RunListItem) =>
  `#${r.id} · ${formatTime(r.created_at)}${r.note ? ` · ${r.note}` : ''} · ${RUN_STATUS[r.status] ?? r.status} · ${r.passed}/${r.done} 通过`

export const MODE: Record<string, string> = { unordered: '不计行顺序', ordered: '按行顺序', scalar: '单个值' }

/** 两边结果对上了多少，一句话说清；旧记录没有比对明细时返回 null。 */
export function compareSummary(r: Pick<Evaluation, 'expected_row_count' | 'actual_row_count' | 'matched_rows' | 'columns_match' | 'order_only' | 'truncated' | 'expected_rows' | 'actual_rows'>): { tone: 'success' | 'danger' | 'warning'; text: string } | null {
  const e = r.expected_row_count
  const a = r.actual_row_count
  if (e == null || a == null) return null
  if (r.truncated) return { tone: 'warning', text: '结果超过返回上限被截断，无法全量比对。' }
  if (r.columns_match === false) {
    const width = (rows?: Record<string, unknown>[]) => Object.keys(rows?.[0] ?? {}).length
    return { tone: 'danger', text: `列数不同：期望 ${width(r.expected_rows)} 列，实际 ${width(r.actual_rows)} 列。` }
  }
  const m = r.matched_rows ?? 0
  if (r.order_only) return { tone: 'danger', text: `${formatInt(e)} 行内容一致，但行顺序不同（这道题要求按顺序）。` }
  const width = (rows?: Record<string, unknown>[]) => Object.keys(rows?.[0] ?? {}).length
  const more = width(r.actual_rows) - width(r.expected_rows)
  const note = more > 0 && r.expected_rows?.length ? `实际多出 ${more} 列，不参与比对。` : ''
  if (m === e && m === a) return { tone: 'success', text: `${formatInt(e)} 行全部一致。${note}` }
  const parts = [`期望 ${formatInt(e)} 行，实际 ${formatInt(a)} 行，其中 ${formatInt(m)} 行一致`]
  if (e > m) parts.push(`实际少了 ${formatInt(e - m)} 行`)
  if (a > m) parts.push(`实际多出 ${formatInt(a - m)} 行`)
  return { tone: 'danger', text: `${parts.join('；')}。` }
}


const DAY = 86_400_000
const finished = (r: RunListItem) => r.status === 'success' && r.done > 0
/** 跑了整个题库的评测；挑几道题的复测和整套题的通过率不可比。bankSize 未知（0）时都算。 */
const fullRun = (bankSize: number) => (r: RunListItem) => r.case_count >= bankSize
const rateOf = (r: RunListItem) => r.passed / r.done

export interface OverviewStats {
  /** 最近一次完成的评测。 */
  latest: RunListItem | null
  passRate: number | null
  /** 比上一次完成的评测变化多少个百分点；没有上一次时为 null。 */
  deltaPt: number | null
  count30: number
  /** 已完成评测的平均用时（毫秒）；没有时为 null。 */
  avgMs: number | null
}

/** 概览统计卡：通过率、变化和用时只看已跑完整个题库的评测，以最近一次为准；次数统计全部评测。 */
export function overviewStats(runs: RunListItem[], now = Date.now(), bankSize = 0): OverviewStats {
  const done = runs.filter((r) => finished(r) && fullRun(bankSize)(r)).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
  const latest = done[0] ?? null
  const prev = done[1]
  const timed = done.filter((r) => r.duration_ms > 0)
  return {
    latest,
    passRate: latest ? rateOf(latest) : null,
    deltaPt: latest && prev ? Math.round((rateOf(latest) - rateOf(prev)) * 1000) / 10 : null,
    count30: runs.filter((r) => now - Date.parse(r.created_at) <= 30 * DAY).length,
    avgMs: timed.length ? Math.round(timed.reduce((s, r) => s + r.duration_ms, 0) / timed.length) : null,
  }
}

/** 趋势图用的评测：已跑完整个题库的，按时间从早到晚；range 为最近几次（'10'）或最近几天（'30d'）。 */
export function trendRuns(runs: RunListItem[], range: { last: number } | { days: number }, now = Date.now(), bankSize = 0): RunListItem[] {
  const done = runs.filter((r) => finished(r) && fullRun(bankSize)(r)).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  if ('last' in range) return done.slice(-range.last)
  return done.filter((r) => now - Date.parse(r.created_at) <= range.days * DAY)
}

/** 带正负号的百分点变化，如 +3.1 pt；没有变化写 0 pt。 */
export const formatPt = (v: number) => `${v > 0 ? '+' : ''}${v} pt`

/** 通过率或耗时的变化；before 为 0 或缺失时返回 null。 */
export function changeRatio(before: number, after: number): number | null {
  return before > 0 ? Math.round(((after - before) / before) * 1000) / 10 : null
}
