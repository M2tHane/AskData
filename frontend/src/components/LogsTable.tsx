import type { QueryView } from '../api/types'
import { friendlyError } from '../lib/errors'
import { formatMs, formatTime } from '../lib/format'
import { RunStatusBadge, type RunStatus } from './chat'
import { Icon } from './Icon'

/** 用户主动停止的查询按「失败」存储，展示时单独算作「已取消」。 */
export const isCancelled = (q: QueryView) => q.status === 'failed' && q.error === 'QUERY_CANCELLED'

export function runStatus(q: QueryView): RunStatus {
  if (isCancelled(q)) return 'cancelled'
  if (q.status === 'blocked') return 'blocked'
  if (q.status === 'failed') return 'error'
  if (q.status === 'clarify') return 'clarify'
  return q.retry_count > 0 ? 'fixed' : 'ok'
}

interface RerunOf {
  question?: string
  query_run_id?: number | null
}

export const snapshotOf = (q: QueryView) => (q.context_snapshot ?? {}) as Record<string, unknown>
export const rerunOf = (q: QueryView) => snapshotOf(q).rerun_of as RerunOf | null | undefined

/** 第二行：失败 / 拦截写原因，澄清写要澄清什么，成功写行数和耗时。 */
function secondLine(q: QueryView): { text: string; danger: boolean } {
  if (isCancelled(q)) return { text: '用户主动停止', danger: false }
  if (q.status === 'blocked' || q.status === 'failed') {
    const code = q.status === 'blocked' ? q.blocked_code : q.error
    return { text: friendlyError(code ?? undefined).title, danger: true }
  }
  if (q.status === 'clarify') {
    const c = snapshotOf(q).clarification as { question?: string } | null | undefined
    return { text: c?.question ?? '问题存在歧义，等待用户选择', danger: false }
  }
  return { text: `${q.row_count} 行 · ${formatMs(q.duration_ms)}${q.retry_count ? ` · 纠错 ${q.retry_count} 次` : ''}`, danger: false }
}

/** 查询日志表：状态、问题、数据源、反馈、用户、编号、时间、查看。 */
export function LogsTable(props: {
  rows: QueryView[]
  datasourceName: (id: number) => string
  personName: (id: number) => string
  onOpen: (q: QueryView) => void
}) {
  return (
    <table className="cb-data cb-data-fixed">
      <thead>
        <tr>
          <th style={{ width: 112 }}>状态</th>
          <th>问题</th>
          <th style={{ width: '14%' }}>数据源</th>
          <th style={{ width: 56 }}>反馈</th>
          <th style={{ width: '13%' }}>用户</th>
          <th style={{ width: 84 }}>编号</th>
          <th style={{ width: 112 }}>时间</th>
          <th style={{ width: 56 }}><span className="cb-sr">操作</span></th>
        </tr>
      </thead>
      <tbody>
        {props.rows.map((q) => {
          const line = secondLine(q)
          const question = rerunOf(q)?.question ? `重跑：${rerunOf(q)?.question}` : q.question
          return (
            <tr key={q.id}>
              <td><RunStatusBadge status={runStatus(q)} /></td>
              <td title={question}>
                <span className="cb-lg-q">{question}</span>
                <span className={line.danger ? 'cb-sub cb-lg-reason cb-lg-q' : 'cb-sub cb-lg-q'}>{line.text}</span>
              </td>
              <td title={props.datasourceName(q.datasource_id)}>{props.datasourceName(q.datasource_id)}</td>
              <td>{q.has_feedback ? <span title="用户给过反馈"><Icon name="flag" label="有反馈" /></span> : <span className="cb-faint">—</span>}</td>
              <td title={props.personName(q.user_id)}>{props.personName(q.user_id)}</td>
              <td className="cb-m">q_{q.id}</td>
              <td className="cb-r" title={q.created_at}>{formatTime(q.created_at)}</td>
              <td>
                <span className="cb-actions">
                  <button type="button" className="cb-link" onClick={() => props.onOpen(q)}>查看</button>
                </span>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
