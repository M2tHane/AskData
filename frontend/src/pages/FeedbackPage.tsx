import { useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useHandleFeedback } from '../api/hooks'
import type { FeedbackView, Page, QueryView } from '../api/types'
import { Badge, Button, Callout, Segmented, Tabs } from '../components/basic'
import { Check } from '../components/Check'
import { Dialog, DialogFooter, Drawer } from '../components/Dialog'
import { SqlBlock } from '../components/chat'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { Pager } from '../components/Pager'
import { HeaderActions } from '../components/PageHeader'
import { ResultPreview } from '../components/ReviewParts'
import { ReviewFilter, rangeQuery, usePersonName, type ReviewRange } from '../components/ReviewFilter'
import { Select } from '../components/Select'
import { StatCard, Stats } from '../components/StatCard'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { badRate, isEvalConverted, REASON, reasonLabel, snapshotSql, topReason } from '../lib/feedbackStats'
import { formatInt, formatTime } from '../lib/format'
import { useAuth } from '../state/auth'

const PAGE = 10
/** 统计和「评价 / 原因」筛选在前端做：最多取最近 500 条反馈。 */
const CAP = 500
const DAY = 86_400_000

type Rating = '' | 'down' | 'up'

async function fetchAll(query: Record<string, string | number | undefined>): Promise<{ items: FeedbackView[]; total: number }> {
  const items: FeedbackView[] = []
  let total = 0
  for (let page = 1; items.length < CAP; page++) {
    const r = await api<Page<FeedbackView>>('GET', '/api/feedback', { query: { ...query, page, page_size: 100 } })
    total = r.total
    items.push(...r.items)
    if (!r.items.length || items.length >= r.total) break
  }
  return { items, total }
}

interface ListArgs {
  status: string
  range: ReviewRange
  rating: Rating
  reason: string
  page: number
}

/** 没有「评价 / 原因」筛选时由服务端分页；有筛选时取回全部再在前端筛选分页。 */
function useFeedbackList(a: ListArgs) {
  return useQuery({
    queryKey: ['feedback', 'list', a],
    placeholderData: keepPreviousData,
    queryFn: async (): Promise<{ items: FeedbackView[]; total: number }> => {
      const base = { status: a.status || undefined, ...rangeQuery(a.range) }
      if (!a.rating && !a.reason) {
        const r = await api<Page<FeedbackView>>('GET', '/api/feedback', { query: { ...base, page: a.page, page_size: PAGE } })
        return { items: r.items, total: r.total }
      }
      const all = (await fetchAll(base)).items.filter((f) => (!a.rating || f.rating === a.rating) && (!a.reason || f.reason_code === a.reason))
      return { items: all.slice((a.page - 1) * PAGE, a.page * PAGE), total: all.length }
    },
  })
}

/** 统计卡：不受筛选影响，按全部反馈和近 7 天的查询总数算。 */
function useFeedbackStats() {
  return useQuery({
    queryKey: ['feedback', 'stats'],
    queryFn: async () => {
      const since = new Date(Date.now() - 7 * DAY).toISOString()
      const [all, queries] = await Promise.all([
        fetchAll({}),
        api<Page<QueryView>>('GET', '/api/querylog', { query: { since, page: 1, page_size: 1 } }),
      ])
      const recentDowns = all.items.filter((f) => f.rating === 'down' && f.created_at >= since).length
      return {
        open: all.items.filter((f) => f.status === 'open').length,
        total: all.total,
        capped: all.total > all.items.length,
        rate: badRate(recentDowns, queries.total),
        top: topReason(all.items),
        converted: all.items.filter(isEvalConverted).length,
      }
    },
  })
}

function useConvert() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: number; sql: string }) =>
      api<FeedbackView>('POST', `/api/feedback/${v.id}/convert`, { body: { kind: 'eval', expected: { expected_sql: v.sql } } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['feedback'] }),
  })
}

const failure = (e: unknown) => (e instanceof ApiError ? describeError(e.code) : '请稍后重试。')
const questionOf = (f: FeedbackView) => (typeof f.snapshot?.question === 'string' && f.snapshot.question ? f.snapshot.question : `q_${f.query_run_id}`)

const RANGE_ACTIVE = (r: ReviewRange) => r.userId != null || !!r.from || !!r.to

export function FeedbackPage() {
  const toast = useToast()
  const role = useAuth((s) => s.user?.role)
  const canAct = role === 'admin' || role === 'leader'
  const [status, setStatus] = useState('open')
  const [page, setPage] = useState(1)
  const [range, setRange] = useState<ReviewRange>({})
  const [rating, setRating] = useState<Rating>('')
  const [reason, setReason] = useState('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [open, setOpen] = useState<FeedbackView | null>(null)
  const [converting, setConverting] = useState<FeedbackView | null>(null)
  const [busy, setBusy] = useState(false)

  const list = useFeedbackList({ status, range, rating, reason, page })
  const stats = useFeedbackStats()
  const personName = usePersonName()
  const handle = useHandleFeedback()
  const items = useMemo(() => list.data?.items ?? [], [list.data])
  const total = list.data?.total ?? 0
  const filtered = !!rating || !!reason || RANGE_ACTIVE(range)

  const pending = items.filter((f) => f.status === 'open')
  const chosen = pending.filter((f) => selected.has(f.id))
  const allState = chosen.length === 0 ? 'off' : chosen.length === pending.length ? 'on' : 'some'
  const reset = (fn: () => void) => {
    fn()
    setPage(1)
    setSelected(new Set())
  }
  const toggle = (id: number, on: boolean) =>
    setSelected((s) => {
      const next = new Set(s)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  const markHandled = async (f: FeedbackView) => {
    try {
      await handle.mutateAsync(f.id)
      toast({ tone: 'success', title: '已标记为已处理', message: questionOf(f) })
      setOpen((o) => (o?.id === f.id ? { ...o, status: 'handled' } : o))
    } catch (e) {
      toast({ tone: 'danger', title: '标记失败', message: failure(e) })
    }
  }

  const markMany = async () => {
    setBusy(true)
    const results = await Promise.allSettled(chosen.map((f) => handle.mutateAsync(f.id)))
    setBusy(false)
    const failed = results.filter((r) => r.status === 'rejected').length
    setSelected(new Set())
    toast(
      failed
        ? { tone: 'warning', title: `已处理 ${results.length - failed} 条，${failed} 条失败`, message: '失败的反馈仍在待处理里，可以重试。' }
        : { tone: 'success', title: `已将 ${results.length} 条标记为已处理` },
    )
  }

  const s = stats.data
  const tabs = [
    { id: 'open', label: '待处理', count: s?.open },
    { id: 'handled', label: '已处理' },
    { id: '', label: '全部' },
  ]

  return (
    <div className="cb-page">
      <HeaderActions>
        <Button icon="refresh" onClick={() => { void list.refetch(); void stats.refetch() }}>刷新</Button>
      </HeaderActions>
      <Stats>
        <StatCard label="待处理" value={s ? formatInt(s.open) : '—'} />
        <StatCard
          label="近 7 天差评率"
          value={s?.rate != null ? `${(s.rate * 100).toFixed(1)}%` : '—'}
          help="近 7 天的没用评价数 ÷ 近 7 天的查询总数"
          title={s?.capped ? `反馈超过 ${CAP} 条，只统计了最近 ${CAP} 条` : undefined}
        />
        <StatCard label="最常见原因" value={s?.top ? reasonLabel(s.top.code) : '—'} help="没用评价里出现最多的原因" title={s?.top ? `${s.top.count} 条` : undefined} />
        <StatCard label="已转评测题" value={s ? formatInt(s.converted) : '—'} />
      </Stats>
      <Tabs label="处理状态" value={status} onChange={(v) => reset(() => setStatus(v))} items={tabs} />
      <div className="cb-filters">
        <Segmented
          label="评价"
          value={rating}
          onChange={(v) => reset(() => setRating(v))}
          items={[{ id: '', label: '全部评价' }, { id: 'down', label: '没用' }, { id: 'up', label: '有用' }]}
        />
        <Select
          ariaLabel="按原因筛选"
          value={reason}
          onChange={(v) => reset(() => setReason(v))}
          options={[{ value: '', label: '全部原因' }, ...Object.entries(REASON).map(([value, label]) => ({ value, label }))]}
        />
        <ReviewFilter value={range} onChange={(r) => reset(() => setRange(r))} />
        <span className="cb-spacer" />
        {canAct ? (
          <Button disabled={!chosen.length || busy} onClick={() => void markMany()}>
            {chosen.length ? `批量标记已处理（${chosen.length}）` : '批量标记已处理'}
          </Button>
        ) : null}
      </div>
      <ListCard label="反馈列表" pager={<Pager page={page} pageSize={PAGE} total={total} onPage={(p) => { setPage(p); setSelected(new Set()) }} />}>
        <ListStatus
          loading={list.isPending}
          error={list.error}
          onRetry={() => void list.refetch()}
          what="反馈"
          count={items.length}
          filtered={filtered}
          onClear={() => reset(() => { setRating(''); setReason(''); setRange({}) })}
          cols={7}
          empty={
            <EmptyState icon="flag" title={status === 'open' ? '没有待处理的反馈' : status === 'handled' ? '还没有已处理的反馈' : '还没有反馈'}>
              {status === 'open' ? '员工在对话里给回答点「没用」后，会出现在这里。' : '员工对回答评价后，会出现在这里。'}
            </EmptyState>
          }
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                <th className="cb-fb-sel">
                  {canAct ? <Check state={allState === 'off' ? 'off' : allState} label="选择本页待处理反馈" disabled={!pending.length} onChange={(on) => setSelected(on ? new Set(pending.map((f) => f.id)) : new Set())} /> : null}
                </th>
                <th style={{ width: 72 }}>评价</th>
                <th style={{ width: 96 }}>原因</th>
                <th style={{ width: '22%' }}>说明</th>
                <th>问题</th>
                <th style={{ width: 88 }}>用户</th>
                <th style={{ width: 112 }}>时间</th>
                <th style={{ width: canAct ? 236 : 104 }}><span className="cb-sr">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {items.map((f) => (
                <tr key={f.id}>
                  <td className="cb-fb-sel">
                    {canAct && f.status === 'open' ? <Check state={selected.has(f.id) ? 'on' : 'off'} label={`选择反馈 ${f.id}`} onChange={(on) => toggle(f.id, on)} /> : null}
                  </td>
                  <td>{f.rating === 'up' ? <Badge tone="success">有用</Badge> : <Badge tone="danger">没用</Badge>}</td>
                  <td title={reasonLabel(f.reason_code)}>{f.reason_code ? reasonLabel(f.reason_code) : <span className="cb-faint">—</span>}</td>
                  <td title={f.comment} className="cb-muted">{f.comment || <span className="cb-faint">—</span>}</td>
                  <td title={questionOf(f)}>
                    <button type="button" className="cb-link cb-fb-q" onClick={() => setOpen(f)}>{questionOf(f)}</button>
                  </td>
                  <td title={personName(f.user_id)}>{personName(f.user_id)}</td>
                  <td className="cb-r" title={f.created_at}>{formatTime(f.created_at)}</td>
                  <td>
                    <span className="cb-fb-acts">
                      {f.status === 'handled' ? (
                        <Badge tone="success">{isEvalConverted(f) ? '已转评测题' : '已处理'}</Badge>
                      ) : canAct ? (
                        <>
                          <Button size="sm" variant="ghost" onClick={() => setConverting(f)}>转为评测题</Button>
                          <Button size="sm" disabled={handle.isPending} onClick={() => void markHandled(f)}>标记已处理</Button>
                        </>
                      ) : null}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>
      {open ? (
        <FeedbackDrawer
          f={open}
          personName={personName}
          canAct={canAct}
          onClose={() => setOpen(null)}
          onHandle={() => void markHandled(open)}
          onConvert={() => setConverting(open)}
        />
      ) : null}
      {converting ? (
        <ConvertDialog
          f={converting}
          onClose={() => setConverting(null)}
          onDone={(id) => {
            setConverting(null)
            setOpen((o) => (o?.id === id ? { ...o, status: 'handled', converted_ref: 'eval:' } : o))
            toast({ tone: 'success', title: '已转为评测题', message: '可以在评测页的题库里看到。' })
          }}
        />
      ) : null}
    </div>
  )
}

function FeedbackDrawer(props: { f: FeedbackView; personName: (id: number) => string; canAct: boolean; onClose: () => void; onHandle: () => void; onConvert: () => void }) {
  const { f } = props
  const snap = f.snapshot as Partial<QueryView> & Record<string, unknown>
  const sql = snapshotSql(f)
  const preview = Array.isArray(snap.result_preview) ? snap.result_preview : []
  const context = (snap.context_snapshot ?? {}) as Record<string, unknown>
  const interpretation = typeof context.interpretation === 'string' ? context.interpretation : ''
  const open = f.status === 'open'
  return (
    <Drawer
      title="反馈详情"
      wide
      onClose={props.onClose}
      footer={
        props.canAct && open ? (
          <>
            <Button onClick={props.onConvert}>转为评测题</Button>
            <Button variant="primary" onClick={props.onHandle}>标记已处理</Button>
          </>
        ) : undefined
      }
    >
      <dl className="cb-kv">
        <dt>评价</dt>
        <dd>{f.rating === 'up' ? <Badge tone="success">有用</Badge> : <Badge tone="danger">没用</Badge>}</dd>
        <dt>原因</dt>
        <dd>{f.reason_code ? reasonLabel(f.reason_code) : <span className="cb-faint">未选择</span>}</dd>
        <dt>说明</dt>
        <dd>{f.comment || <span className="cb-faint">没有填写</span>}</dd>
        <dt>用户</dt>
        <dd>{props.personName(f.user_id)}</dd>
        <dt>时间</dt>
        <dd>{formatTime(f.created_at)}</dd>
        <dt>状态</dt>
        <dd>{open ? <Badge tone="warning">待处理</Badge> : <Badge tone="success">{isEvalConverted(f) ? '已转评测题' : '已处理'}</Badge>}</dd>
      </dl>
      <section>
        <h3 className="cb-h3">问题</h3>
        <p>{questionOf(f)}</p>
      </section>
      {snap.status && snap.status !== 'success' ? (
        <Callout tone="warning" title="当时这次查询没有成功">
          状态：{snap.status === 'blocked' ? '已拦截' : snap.status === 'failed' ? '失败' : '待澄清'}
          {snap.error || snap.blocked_code ? <> · <span className="cb-m">{String(snap.blocked_code ?? snap.error)}</span></> : null}
        </Callout>
      ) : null}
      {interpretation ? (
        <section>
          <h3 className="cb-h3">当时的回答</h3>
          <p>{interpretation}</p>
        </section>
      ) : null}
      <section>
        <h3 className="cb-h3">当时的 SQL</h3>
        {sql ? <SqlBlock sql={sql} defaultOpen /> : <p className="cb-muted">这次查询没有生成 SQL。</p>}
      </section>
      <section>
        <h3 className="cb-h3">当时的结果{typeof snap.row_count === 'number' ? <span className="cb-muted"> · 共 {formatInt(snap.row_count)} 行，预览前 {preview.length} 行</span> : null}</h3>
        <ResultPreview rows={preview as Record<string, unknown>[]} />
      </section>
    </Drawer>
  )
}

function ConvertDialog(props: { f: FeedbackView; onClose: () => void; onDone: (id: number) => void }) {
  const toast = useToast()
  const convert = useConvert()
  const [sql, setSql] = useState(snapshotSql(props.f))
  const submit = async () => {
    try {
      await convert.mutateAsync({ id: props.f.id, sql: sql.trim() })
      props.onDone(props.f.id)
    } catch (e) {
      toast({ tone: 'danger', title: '转为评测题失败', message: failure(e) })
    }
  }
  return (
    <Dialog title="转为评测题" onClose={props.onClose}>
      <p>{questionOf(props.f)}</p>
      <label className="cb-h3" htmlFor="cb-fb-sql">期望的 SQL</label>
      <textarea id="cb-fb-sql" className="cb-fb-sql" value={sql} spellCheck={false} onChange={(e) => setSql(e.target.value)} />
      <p className="cb-muted">评测会拿这条 SQL 的结果当标准答案，请先改成正确的写法。转换后这条反馈自动记为已处理。</p>
      <DialogFooter>
        <Button onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={!sql.trim() || convert.isPending} onClick={() => void submit()}>转为评测题</Button>
      </DialogFooter>
    </Dialog>
  )
}
