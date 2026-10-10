import { useState } from 'react'
import { fetchQuery, useModels, useQueryLog } from '../api/hooks'
import type { QueryView } from '../api/types'
import { Badge, Button, Callout, Panel, Tabs } from '../components/basic'
import { RunList, type RunStatus } from '../components/chat'
import { Dialog } from '../components/Dialog'
import { Pager } from '../components/Pager'
import { SqlBlock } from '../components/chat'
import { ReviewFilter, rangeQuery, usePersonName, type ReviewRange } from '../components/ReviewFilter'
import { describeError, friendlyError } from '../lib/errors'
import { formatInt, formatMs, formatTime } from '../lib/format'

/** 用户主动停止的查询按「失败」存储，展示时单独算作「已取消」。 */
export const isCancelled = (q: QueryView) => q.status === 'failed' && q.error === 'QUERY_CANCELLED'

export function runStatus(q: QueryView): RunStatus {
  if (isCancelled(q)) return 'cancelled'
  if (q.status === 'blocked') return 'blocked'
  if (q.status === 'failed') return 'error'
  if (q.status === 'clarify') return 'clarify'
  return q.retry_count > 0 ? 'fixed' : 'ok'
}

const STATUS: Record<string, { label: string; tone: 'success' | 'danger' | 'warning' | 'info' | 'neutral' }> = {
  success: { label: '成功', tone: 'success' },
  blocked: { label: '已拦截', tone: 'danger' },
  failed: { label: '失败', tone: 'danger' },
  clarify: { label: '待澄清', tone: 'info' },
  cancelled: { label: '已取消', tone: 'neutral' },
}

const STAGE: Record<string, string> = {
  load_context: '读取对话上下文',
  visible_scope: '计算可见范围',
  retrieval: '检索语义与指标',
  clarification: '澄清判断',
  generate_sql: '生成 SQL',
  guard: 'SQL 安全校验',
  execute: '执行查询',
  correction: '自动纠错',
  presentation: '生成图表与回答',
}

interface RerunOf {
  question?: string
  query_run_id?: number | null
}

const snapshot = (q: QueryView) => (q.context_snapshot ?? {}) as Record<string, unknown>
export const rerunOf = (q: QueryView) => snapshot(q).rerun_of as RerunOf | null | undefined

/** 最后一个开始执行的环节：失败、拦截都停在这里。 */
export function lastStage(q: QueryView): string | undefined {
  const names = Object.keys(q.stage_timings ?? {})
  return names[names.length - 1]
}

const FILTERS = [
  { id: '', label: '全部' },
  { id: 'success', label: '成功' },
  { id: 'blocked', label: '已拦截' },
  { id: 'failed', label: '失败' },
  { id: 'clarify', label: '待澄清' },
  { id: 'cancelled', label: '已取消' },
]

export function LogsPage() {
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<QueryView | null>(null)
  const [range, setRange] = useState<ReviewRange>({})
  const { data } = useQueryLog({ status: status || undefined, ...rangeQuery(range), page })
  const personName = usePersonName()
  const items = data?.items ?? []
  const blocked = items.filter((q) => q.status === 'blocked').length

  return (
    <div className="cb-page">
      <div className="cb-grid-stats">
        <Panel><div className="cb-stat"><b>{formatInt(data?.total ?? 0)}</b><span>查询总数（当前筛选）</span></div></Panel>
        <Panel><div className="cb-stat"><b>{blocked}</b><span>本页已拦截</span></div></Panel>
        <Panel><div className="cb-stat"><b>{items.filter((q) => q.retry_count > 0).length}</b><span>本页纠错后成功</span></div></Panel>
      </div>
      <Panel title="查询日志">
        <div className="cb-toolbar">
          <Tabs label="状态筛选" items={FILTERS} value={status} onChange={(id) => { setStatus(id); setPage(1) }} />
          <span className="cb-spacer" />
          <ReviewFilter value={range} onChange={(r) => { setRange(r); setPage(1) }} />
        </div>
        <RunList
          rows={items.map((q) => ({
            key: q.id,
            status: runStatus(q),
            question: rerunOf(q)?.question ? `重跑：${rerunOf(q)?.question}` : q.question,
            reason: q.status === 'blocked' && q.blocked_code ? describeError(q.blocked_code) : undefined,
            detail: `${q.row_count} 行 · ${formatMs(q.duration_ms)}${q.retry_count ? ` · 纠错 ${q.retry_count} 次` : ''}`,
            user: personName(q.user_id),
            id: `q_${q.id}`,
            time: formatTime(q.created_at),
          }))}
          onOpen={(key) => setOpen(items.find((q) => q.id === key) ?? null)}
        />
        {data && !items.length ? <div className="cb-muted">没有符合条件的查询。</div> : null}
        <Pager page={page} pageSize={20} total={data?.total ?? 0} onPage={setPage} />
      </Panel>
      {open ? <QueryDetail query={open} personName={personName} onOpen={setOpen} onClose={() => setOpen(null)} /> : null}
    </div>
  )
}

function QueryDetail(props: { query: QueryView; personName: (id: number) => string; onOpen: (q: QueryView) => void; onClose: () => void }) {
  const q = props.query
  const models = useModels()
  const modelName = models.data?.items.find((m) => String(m.id) === q.model_id)?.model_name
  const cancelled = isCancelled(q)
  const status = STATUS[cancelled ? 'cancelled' : q.status] ?? { label: q.status, tone: 'info' as const }
  const source = rerunOf(q)
  const clarification = snapshot(q).clarification as { question?: string; choices?: string[] } | null | undefined
  const interpretation = snapshot(q).interpretation as string | undefined
  const retries = (snapshot(q).retries ?? []) as { attempt?: number; code?: string }[]
  const code = q.status === 'blocked' ? q.blocked_code : q.error
  const reason = code && !cancelled ? friendlyError(code) : null
  const stage = lastStage(q)
  const openSource = async () => {
    if (source?.query_run_id) props.onOpen(await fetchQuery(source.query_run_id))
  }

  return (
    <Dialog title={`查询 q_${q.id}`} size="xl" onClose={props.onClose}>
      <dl className="cb-detail cb-detail-wide">
        <dt>问题</dt>
        <dd>{source ? '手动编辑 SQL 后重跑' : q.question}</dd>
        {source ? (
          <>
            <dt>原问题</dt>
            <dd>
              {source.question || <span className="cb-muted">（原回答没有记录问题）</span>}
              {source.query_run_id ? (
                <button type="button" className="cb-link cb-detail-ref" onClick={() => void openSource()}>
                  q_{source.query_run_id}
                </button>
              ) : null}
            </dd>
          </>
        ) : null}
        <dt>用户</dt>
        <dd>{props.personName(q.user_id)}</dd>
        <dt>状态</dt>
        <dd><Badge tone={status.tone}>{status.label}</Badge></dd>
        <dt>时间</dt>
        <dd>{formatTime(q.created_at)}</dd>
        <dt>模型</dt>
        <dd>{q.model_id ? <span className="cb-mono">{modelName ?? `模型 ${q.model_id}`}</span> : <span className="cb-muted">未调用模型</span>}</dd>
        <dt>耗时</dt>
        <dd>{formatMs(q.duration_ms)}</dd>
        <dt>Token</dt>
        <dd className="cb-num">输入 {formatInt(q.tokens_in)} · 输出 {formatInt(q.tokens_out)}</dd>
        {q.status === 'success' ? (<><dt>结果</dt><dd>{formatInt(q.row_count)} 行{q.retry_count ? ` · 自动纠错 ${q.retry_count} 次` : ''}</dd></>) : null}
        {interpretation ? (<><dt>理解</dt><dd>{interpretation}</dd></>) : null}
      </dl>

      {reason ? (
        <Callout tone={q.status === 'blocked' ? 'warning' : 'danger'} title={`${status.label}原因：${reason.title}`}>
          {reason.hint ? <div>{reason.hint}</div> : null}
          <div className="cb-muted">
            {stage ? `停在「${STAGE[stage] ?? stage}」环节 · ` : ''}错误码 <span className="cb-mono">{reason.code}</span>
          </div>
          {retries.length ? <div className="cb-muted">自动纠错 {retries.length} 次：{retries.map((r) => r.code).join('、')}</div> : null}
        </Callout>
      ) : null}

      {cancelled ? (
        <Callout tone="info" title="用户主动停止了这次查询">
          <div>不是系统故障，不计入失败。</div>
          {stage ? <div className="cb-muted">停止时正在「{STAGE[stage] ?? stage}」环节</div> : null}
        </Callout>
      ) : null}

      {q.status === 'clarify' ? (
        <Callout tone="info" title={`为什么要澄清：${clarification?.question ?? '问题存在歧义'}`}>
          <div>{q.tokens_in > 0 ? '模型判断这个问题有多种理解，需要用户先选一个口径。' : '规则检测到问题命中了多个同名口径或缺少必要条件，没有调用模型。'}</div>
          {clarification?.choices?.length ? (
            <div className="cb-row" style={{ flexWrap: 'wrap', marginTop: 'var(--space-2)' }}>
              {clarification.choices.map((c) => <Badge key={c} outline>{c}</Badge>)}
            </div>
          ) : null}
        </Callout>
      ) : null}

      {q.normalized_sql ? <SqlBlock sql={q.normalized_sql} defaultOpen /> : null}
      <div className="cb-row" style={{ justifyContent: 'flex-end' }}>
        <Button onClick={props.onClose}>关闭</Button>
      </div>
    </Dialog>
  )
}
