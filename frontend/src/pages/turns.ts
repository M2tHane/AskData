import { formatStamp } from '../lib/format'
import type { AnswerMeta, ChartSpec, Clarification, ErrorCode, QueryReply, StreamEvent, SuggestedQuestion, SuggestedQuestions } from '../api/types'

export type TurnStatus = 'streaming' | QueryReply['status']

export interface Turn {
  key: string
  question: string
  status: TurnStatus
  stages: string[]
  sql?: string
  columns: string[]
  rows: Record<string, unknown>[]
  truncated: boolean
  rowCount: number
  previewTruncated: boolean
  chart?: ChartSpec
  /** 模型的思考过程（生成 SQL 时），流式累积。 */
  reasoning: string
  reasoningStartedAt?: number
  reasoningEndedAt?: number
  /** 由查询结果写成的结论，流式累积；失败时是错误说明。 */
  answer: string
  meta?: AnswerMeta
  title?: string
  clarification?: Clarification
  clarifyMessageId?: number
  picked?: string
  errorCode?: ErrorCode
  queryRunId?: number | null
  messageId?: number | null
  startedAt: number
  ms?: number
  /** 历史回答：结果是什么时候查的；reread 表示原结果没保存，是后来重新查的。 */
  queriedAt?: string
  reread?: boolean
  /** 有字段现在要脱敏而当时没有：原结论已隐藏，数据按现在的规则脱敏。 */
  masksChanged?: boolean
}

export const STAGE_LABEL: Record<string, string> = {
  load_context: '加载会话上下文',
  visible_scope: '确定可见范围',
  retrieval: '检索可见表和字段',
  clarification: '检查是否有歧义',
  generate_sql: '理解问题并生成 SQL',
  sql_guard: '校验 SQL',
  execute: '执行查询',
  correction: '自动纠错',
  presentation: '整理结果并写结论',
  record: '记录日志',
}

export function newTurn(question: string, now = Date.now()): Turn {
  return {
    key: `t${now}-${Math.random().toString(36).slice(2, 7)}`,
    question,
    status: 'streaming',
    stages: [],
    columns: [],
    rows: [],
    truncated: false,
    rowCount: 0,
    previewTruncated: false,
    reasoning: '',
    answer: '',
    startedAt: now,
  }
}

/** 思考结束于第一个非思考产物（SQL、结论或结束）。 */
const endThinking = (turn: Turn, now: number) => (turn.reasoning && turn.reasoningEndedAt == null ? { reasoningEndedAt: now } : {})

export function applyEvent(turn: Turn, ev: StreamEvent, now = Date.now()): Turn {
  switch (ev.event) {
    case 'stage':
      return { ...turn, stages: [...turn.stages, ev.data.name] }
    case 'reasoning':
      return { ...turn, reasoning: turn.reasoning + ev.data.delta, reasoningStartedAt: turn.reasoningStartedAt ?? now }
    case 'sql':
      return { ...turn, sql: ev.data.sql, ...endThinking(turn, now) }
    case 'data':
      return { ...turn, columns: ev.data.columns, rows: ev.data.rows, truncated: ev.data.truncated, rowCount: ev.data.row_count ?? ev.data.rows.length, previewTruncated: ev.data.preview_truncated ?? false }
    case 'chart':
      return { ...turn, chart: ev.data }
    case 'explanation':
      return ev.data.clarification ? { ...turn, clarification: ev.data.clarification, ...endThinking(turn, now) } : { ...turn, meta: ev.data.meta ?? turn.meta }
    case 'answer':
      return { ...turn, answer: turn.answer + ev.data.delta, ...endThinking(turn, now) }
    case 'title':
      return { ...turn, title: ev.data.title }
    case 'error':
      return { ...turn, errorCode: ev.data.code, answer: turn.answer || ev.data.message, ...endThinking(turn, now) }
    case 'done':
      return {
        ...turn,
        status: ev.data.status,
        queryRunId: ev.data.query_run_id ?? null,
        messageId: ev.data.message_id ?? null,
        // 澄清的选项要回传这条消息的 id，done 事件里就有，不必再拉一次消息列表。
        clarifyMessageId: ev.data.status === 'clarify' ? (ev.data.message_id ?? undefined) : turn.clarifyMessageId,
        ms: now - turn.startedAt,
        ...endThinking(turn, now),
      }
    default:
      return turn
  }
}

export function turnFromReply(question: string, reply: QueryReply, now = Date.now()): Turn {
  return {
    ...newTurn(question, now),
    status: reply.status,
    sql: reply.sql ?? undefined,
    columns: reply.columns ?? [],
    rows: reply.rows ?? [],
    truncated: reply.truncated,
    rowCount: reply.row_count ?? (reply.rows ?? []).length,
    previewTruncated: reply.preview_truncated ?? false,
    chart: reply.chart ?? undefined,
    answer: legacyAnswer(reply.explanation),
    meta: reply.meta ?? undefined,
    reasoning: reply.meta?.reasoning ?? '',
    clarification: reply.clarification ?? undefined,
    clarifyMessageId: reply.message_id ?? undefined,
    errorCode: reply.code === 'OK' ? undefined : reply.code,
    queryRunId: reply.query_run_id,
    messageId: reply.message_id,
    ms: 0,
    queriedAt: reply.queried_at ?? undefined,
    reread: reply.reread ?? false,
    masksChanged: reply.masks_changed ?? false,
  }
}

/** 结果卡片上的数据来源说明；刚答完的回答不需要。 */
export function provenance(turn: Pick<Turn, 'queriedAt' | 'reread'>, now = new Date()): string | undefined {
  if (!turn.queriedAt) return undefined
  const at = formatStamp(turn.queriedAt, now)
  return turn.reread ? `重新查询于 ${at}（原结果未保存）` : `查询于 ${at}`
}

/** 权限收回后的说明：列出不可见的表和字段；说不出具体字段时（旧回答）给概括说法。 */
export function revokedText(fields: string[]): string {
  const what = fields.length ? `${fields.join('、')} 你已无权查看` : '这条回答用到的部分表或字段你已无权查看'
  return `${what}，思考过程、结论和图表不再显示。`
}

/**
 * 旧版回答的正文是给审计看的技术说明（表名、语义编号、行数），后面还拼着口径和 SQL，
 * 不是给用户的结论；返回空串，由调用方按结果数据给出兜底结论。
 */
export function legacyAnswer(content: string): string {
  return content.includes('\n生效语义/指标：') || content.includes('\n过滤条件与时间范围以已校验 SQL 为准') ? '' : content
}

/** 后端算好的时间范围，每项如「上个月：2026-09-01 至 2026-09-30」。 */
export function listTimeRanges(ranges: AnswerMeta['time_ranges']): string[] {
  return Object.entries(ranges ?? {}).map(([label, value]) => {
    const start = value.start ?? ''
    const end = value.end_exclusive ? shiftDay(value.end_exclusive, -1) : ''
    return start && end ? `${label}：${start} 至 ${end}` : label
  })
}

export function formatTimeRanges(ranges: AnswerMeta['time_ranges']): string | undefined {
  const parts = listTimeRanges(ranges)
  return parts.length ? parts.join(' · ') : undefined
}

/** 回答底部「口径」面板的内容。 */
export function explainOf(meta: AnswerMeta | undefined | null) {
  return {
    definitions: meta?.definitions ?? [],
    filters: meta?.filters ?? [],
    timeRanges: listTimeRanges(meta?.time_ranges),
    notes: meta?.notes ?? [],
  }
}

function shiftDay(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`)
  if (Number.isNaN(d.getTime())) return iso
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

/** 结论没有生成时的兜底句，由数据决定。 */
export function conclusion(rowCount: number, truncated: boolean): string {
  if (rowCount === 0) return '没有查到符合条件的数据。'
  return truncated ? `共返回 ${rowCount.toLocaleString('zh-CN')} 行，结果已截断。` : `共 ${rowCount.toLocaleString('zh-CN')} 行结果。`
}

/** 示例问题：优先用带来源的条目；后端只给了纯文本时，按文本显示、不标来源。 */
export function starters(data: SuggestedQuestions | undefined): SuggestedQuestion[] {
  if (!data) return []
  return data.items?.length ? data.items : data.questions.map((question) => ({ question }))
}
