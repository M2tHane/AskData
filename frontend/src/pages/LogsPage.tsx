import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { fetchQuery, useDatasources, useModels } from '../api/hooks'
import type { Page, QueryView } from '../api/types'
import { Badge, Button, Callout } from '../components/basic'
import { SqlBlock } from '../components/chat'
import { Drawer } from '../components/Dialog'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { isCancelled, LogsTable, rerunOf, runStatus, snapshotOf } from '../components/LogsTable'
import { Pager } from '../components/Pager'
import { HeaderActions, SyncNote } from '../components/PageHeader'
import { ResultPreview } from '../components/ReviewParts'
import { ReviewFilter, rangeQuery, usePersonName, type ReviewRange } from '../components/ReviewFilter'
import { Select } from '../components/Select'
import { StatCard, Stats } from '../components/StatCard'
import { Tabs } from '../components/basic'
import { useToast } from '../components/Toast'
import { ApiError, describeError, friendlyError } from '../lib/errors'
import { formatInt, formatMs, formatTime } from '../lib/format'
import { logStats, stageBars, toCsv } from '../lib/logsStats'

export { isCancelled, rerunOf, runStatus }

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
  record: '写入日志',
}

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

const PAGE = 10
/** 统计和导出最多取最近这么多条。 */
const CAP = 500

interface Filters {
  range: ReviewRange
  datasource: number
  feedbackOnly: boolean
}

const queryOf = (f: Filters, extra: Record<string, string | number | boolean | undefined>) => ({
  ...rangeQuery(f.range),
  datasource_id: f.datasource || undefined,
  has_feedback: f.feedbackOnly || undefined,
  ...extra,
})

async function fetchRows(f: Filters, status: string): Promise<{ rows: QueryView[]; total: number }> {
  const rows: QueryView[] = []
  let total = 0
  for (let page = 1; rows.length < CAP; page++) {
    const r = await api<Page<QueryView>>('GET', '/api/querylog', { query: queryOf(f, { status: status || undefined, page, page_size: 100 }) })
    total = r.total
    rows.push(...r.items)
    if (!r.items.length || rows.length >= r.total) break
  }
  return { rows, total }
}

function useLogList(status: string, f: Filters, page: number) {
  return useQuery({
    queryKey: ['querylog', 'list', status, f, page],
    placeholderData: keepPreviousData,
    queryFn: () => api<Page<QueryView>>('GET', '/api/querylog', { query: queryOf(f, { status: status || undefined, page, page_size: PAGE }) }),
  })
}

/** 统计卡按当前筛选（用户、日期、数据源、只看有反馈）算，不受状态页签影响。 */
function useLogStats(f: Filters) {
  return useQuery({
    queryKey: ['querylog', 'stats', f],
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const [all, blocked] = await Promise.all([fetchRows(f, ''), api<Page<QueryView>>('GET', '/api/querylog', { query: queryOf(f, { status: 'blocked', page: 1, page_size: 1 }) })])
      return { total: all.total, blocked: blocked.total, capped: all.total > all.rows.length, ...logStats(all.rows) }
    },
  })
}

export function LogsPage() {
  const toast = useToast()
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [range, setRange] = useState<ReviewRange>({})
  const [datasource, setDatasource] = useState(0)
  const [feedbackOnly, setFeedbackOnly] = useState(false)
  const [open, setOpen] = useState<QueryView | null>(null)
  const [exporting, setExporting] = useState(false)
  const filters: Filters = { range, datasource, feedbackOnly }

  const list = useLogList(status, filters, page)
  const stats = useLogStats(filters)
  const datasources = useDatasources()
  const personName = usePersonName()
  const items = list.data?.items ?? []
  const dsName = (id: number) => datasources.data?.items.find((d) => d.id === id)?.name ?? `数据源 ${id}`
  const filtered = !!status || datasource !== 0 || feedbackOnly || range.userId != null || !!range.from || !!range.to
  const s = stats.data
  const approx = s?.capped ? `最近 ${CAP} 条` : undefined
  const reset = (fn: () => void) => {
    fn()
    setPage(1)
  }

  const exportCsv = async () => {
    setExporting(true)
    try {
      const { rows, total } = await fetchRows(filters, status)
      const csv = toCsv(
        ['编号', '时间', '状态', '问题', '数据源', '用户', '有反馈', '行数', '耗时(ms)', '纠错次数', '错误码'],
        rows.map((q) => [`q_${q.id}`, q.created_at, STATUS[isCancelled(q) ? 'cancelled' : q.status]?.label ?? q.status, q.question, dsName(q.datasource_id), personName(q.user_id), q.has_feedback ? '是' : '否', q.row_count, q.duration_ms, q.retry_count, q.blocked_code ?? q.error ?? '']),
      )
      const url = URL.createObjectURL(new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `查询日志-${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(url)
      toast(
        total > rows.length
          ? { tone: 'warning', title: `已导出最近 ${formatInt(rows.length)} 条`, message: `当前筛选共 ${formatInt(total)} 条，缩小日期范围可导出更早的记录。` }
          : { tone: 'success', title: `已导出 ${formatInt(rows.length)} 条查询` },
      )
    } catch (e) {
      toast({ tone: 'danger', title: '导出失败', message: e instanceof ApiError ? describeError(e.code) : '请稍后重试。' })
    } finally {
      setExporting(false)
    }
  }

  const index = open ? items.findIndex((q) => q.id === open.id) : -1

  return (
    <div className="cb-page">
      <HeaderActions>
        {list.dataUpdatedAt ? <SyncNote>{`更新于 ${new Date(list.dataUpdatedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`}</SyncNote> : null}
        <Button icon="download" disabled={exporting || !items.length} onClick={() => void exportCsv()}>导出 CSV</Button>
        <Button icon="refresh" onClick={() => { void list.refetch(); void stats.refetch() }}>刷新</Button>
      </HeaderActions>
      <Stats>
        <StatCard label="查询总数（当前筛选）" value={s ? formatInt(s.total) : '—'} />
        <StatCard label="已拦截" value={s ? formatInt(s.blocked) : '—'} />
        <StatCard label="纠错后成功" value={s ? `${s.capped ? '≥' : ''}${formatInt(s.corrected)}` : '—'} title={approx ? `按${approx}统计` : undefined} />
        <StatCard label="平均耗时" value={s?.avgMs != null ? `${s.capped ? '约 ' : ''}${formatMs(s.avgMs)}` : '—'} title={approx ? `按${approx}统计` : undefined} />
      </Stats>
      <Tabs label="状态筛选" items={FILTERS} value={status} onChange={(id) => reset(() => setStatus(id))} />
      <div className="cb-filters">
        <Select
          ariaLabel="按数据源筛选"
          value={datasource}
          onChange={(v) => reset(() => setDatasource(v))}
          options={[{ value: 0, label: '全部数据源' }, ...(datasources.data?.items ?? []).map((d) => ({ value: d.id, label: d.name }))]}
        />
        <ReviewFilter value={range} onChange={(r) => reset(() => setRange(r))} />
        <label className="cb-lg-check">
          <input type="checkbox" checked={feedbackOnly} onChange={(e) => reset(() => setFeedbackOnly(e.target.checked))} />
          只看有反馈
        </label>
      </div>
      <ListCard label="查询日志" pager={<Pager page={page} pageSize={PAGE} total={list.data?.total ?? 0} onPage={setPage} />}>
        <ListStatus
          loading={list.isPending}
          error={list.error}
          onRetry={() => void list.refetch()}
          what="查询日志"
          count={items.length}
          filtered={filtered}
          onClear={() => reset(() => { setStatus(''); setRange({}); setDatasource(0); setFeedbackOnly(false) })}
          cols={7}
          empty={<EmptyState icon="log" title="还没有查询记录">员工在对话里提问后，每次查询都会记录在这里。</EmptyState>}
        >
          <LogsTable rows={items} datasourceName={dsName} personName={personName} onOpen={setOpen} />
        </ListStatus>
      </ListCard>
      {open ? (
        <QueryDetail
          query={open}
          personName={personName}
          datasourceName={dsName}
          onOpen={setOpen}
          onClose={() => setOpen(null)}
          position={index >= 0 ? { index, count: items.length, prev: () => setOpen(items[index - 1] ?? open), next: () => setOpen(items[index + 1] ?? open) } : undefined}
        />
      ) : null}
    </div>
  )
}

interface Position {
  index: number
  count: number
  prev: () => void
  next: () => void
}

function QueryDetail(props: { query: QueryView; personName: (id: number) => string; datasourceName: (id: number) => string; position?: Position; onOpen: (q: QueryView) => void; onClose: () => void }) {
  const q = props.query
  const models = useModels()
  const modelName = models.data?.items.find((m) => String(m.id) === q.model_id)?.model_name
  const cancelled = isCancelled(q)
  const status = STATUS[cancelled ? 'cancelled' : q.status] ?? { label: q.status, tone: 'info' as const }
  const source = rerunOf(q)
  const snap = snapshotOf(q)
  const clarification = snap.clarification as { question?: string; choices?: string[] } | null | undefined
  const interpretation = snap.interpretation as string | undefined
  const retries = (snap.retries ?? []) as { attempt?: number; code?: string }[]
  const code = q.status === 'blocked' ? q.blocked_code : q.error
  const reason = code && !cancelled ? friendlyError(code) : null
  const stage = lastStage(q)
  const bars = stageBars(q.stage_timings)
  const versions = Object.entries(q.prompt_versions ?? {})
  const pos = props.position
  const openSource = async () => {
    if (source?.query_run_id) props.onOpen(await fetchQuery(source.query_run_id))
  }

  return (
    <Drawer
      wide
      title={`查询 q_${q.id}`}
      onClose={props.onClose}
      actions={
        pos ? (
          <span className="cb-drawer-nav">
            <Button variant="ghost" size="sm" icon="chevronLeft" aria-label="上一条" disabled={pos.index <= 0} onClick={pos.prev} />
            <span className="cb-muted cb-r">{pos.index + 1} / {pos.count}</span>
            <Button variant="ghost" size="sm" icon="chevronRight" aria-label="下一条" disabled={pos.index >= pos.count - 1} onClick={pos.next} />
          </span>
        ) : undefined
      }
    >
      <dl className="cb-kv">
        <dt>问题</dt>
        <dd>{source ? '手动编辑 SQL 后重跑' : q.question}</dd>
        {source ? (
          <>
            <dt>原问题</dt>
            <dd>
              {source.question || <span className="cb-muted">（原回答没有记录问题）</span>}
              {source.query_run_id ? (
                <>
                  {' '}
                  <button type="button" className="cb-link" onClick={() => void openSource()}>q_{source.query_run_id}</button>
                </>
              ) : null}
            </dd>
          </>
        ) : null}
        <dt>状态</dt>
        <dd><Badge tone={status.tone}>{status.label}</Badge></dd>
        <dt>用户</dt>
        <dd>{props.personName(q.user_id)}</dd>
        <dt>数据源</dt>
        <dd>{props.datasourceName(q.datasource_id)}</dd>
        <dt>时间</dt>
        <dd>{formatTime(q.created_at)}</dd>
        <dt>模型</dt>
        <dd>{q.model_id ? <span className="cb-m">{modelName ?? `模型 ${q.model_id}`}</span> : <span className="cb-muted">未调用模型</span>}</dd>
        <dt>耗时</dt>
        <dd>{formatMs(q.duration_ms)}</dd>
        <dt>Token</dt>
        <dd className="cb-r">输入 {formatInt(q.tokens_in)} · 输出 {formatInt(q.tokens_out)}</dd>
        {versions.length ? (
          <>
            <dt>提示词</dt>
            <dd className="cb-m">{versions.map(([name, v]) => `${name} ${v}`).join(' · ')}</dd>
          </>
        ) : null}
        {q.status === 'success' ? (<><dt>结果</dt><dd>{formatInt(q.row_count)} 行{q.retry_count ? ` · 自动纠错 ${q.retry_count} 次` : ''}</dd></>) : null}
        {interpretation ? (<><dt>理解</dt><dd>{interpretation}</dd></>) : null}
      </dl>

      {reason ? (
        <Callout tone={q.status === 'blocked' ? 'warning' : 'danger'} title={`${status.label}原因：${reason.title}`}>
          {reason.hint ? <div>{reason.hint}</div> : null}
          <div className="cb-muted">
            {stage ? `停在「${STAGE[stage] ?? stage}」环节 · ` : ''}错误码 <span className="cb-m">{reason.code}</span>
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
            <div className="cb-row">
              {clarification.choices.map((c) => <Badge key={c} outline>{c}</Badge>)}
            </div>
          ) : null}
        </Callout>
      ) : null}

      {bars.length ? (
        <section>
          <h3 className="cb-h3">各环节耗时</h3>
          <ul className="cb-stages">
            {bars.map((b) => (
              <li key={b.name} className="cb-stage">
                <span>{STAGE[b.name] ?? b.name}</span>
                <span className="cb-stage-bar" aria-hidden="true"><i style={{ width: `${b.pct}%` }} /></span>
                <span className="cb-stage-ms">{formatMs(b.ms)}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {q.normalized_sql ? (
        <section>
          <h3 className="cb-h3">SQL</h3>
          <SqlBlock sql={q.normalized_sql} defaultOpen />
        </section>
      ) : null}

      {q.status === 'success' && q.result_preview?.length ? (
        <section>
          <h3 className="cb-h3">结果预览<span className="cb-muted"> · 前 {q.result_preview.length} 行</span></h3>
          <ResultPreview rows={q.result_preview} />
        </section>
      ) : null}
    </Drawer>
  )
}
