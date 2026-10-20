import { Fragment, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { loadEvalCases, useEvalCases, useEvalRuns, useModels } from '../api/hooks'
import type { EvalCaseView, Role, RunListItem } from '../api/types'
import { Button, Segmented, Tabs, TextField } from '../components/basic'
import { EvalCompare } from '../components/EvalCompare'
import { EvalOverview } from '../components/EvalOverview'
import { RunPanel, RunStatus, useRunActions } from '../components/EvalRun'
import { SqlBlock } from '../components/chat'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { Pager, usePaged } from '../components/Pager'
import { Select } from '../components/Select'
import { useToast } from '../components/Toast'
import { ApiError, friendlyError } from '../lib/errors'
import { formatInt, formatSpan, formatTime } from '../lib/format'
import { CATEGORY, DIFFICULTY, EXPECT, RUN_STATUS, canResume, isActive, passRate } from '../lib/evalText'

// 评测文案和判定函数放在 lib/evalText.ts；这里保留原来的导出，别处和测试仍从本文件引用。
export { CATEGORY, compareSummary, looksUnprovisioned, reasonText } from '../lib/evalText'

type View = 'overview' | 'history' | 'bank' | 'compare'

/** 评测开始后的结束通知：看着一次评测从运行中变成完成、中断或停止时弹出，进页面时已经结束的不弹。 */
function useRunToasts(runs: RunListItem[]) {
  const toast = useToast()
  const seen = useRef<Map<number, string>>(new Map())
  useEffect(() => {
    const prev = seen.current
    for (const r of runs) {
      const was = prev.get(r.id)
      if (was && isActive(was) && !isActive(r.status) && r.status !== 'canceled') {
        const title = r.status === 'success' ? `评测 #${r.id} 已完成` : `评测 #${r.id} 已中断`
        toast({ tone: r.status === 'success' ? 'success' : 'warning', title, message: r.done ? `通过率 ${passRate(r)}，${r.passed}/${r.done} 题通过。` : undefined })
      }
      prev.set(r.id, r.status)
    }
  }, [runs, toast])
}

export function EvalPage({ role }: { role: Role }) {
  const admin = role === 'admin'
  const [view, setView] = useState<View>('overview')
  const [picked, setPicked] = useState<number | null>(null)
  const runs = useEvalRuns()
  const cases = useEvalCases()
  const list = runs.data ?? []
  useRunToasts(list)

  const open = (id: number) => {
    setPicked(id)
    setView('history')
  }

  return (
    <div className="cb-page">
      <Tabs
        label="评测"
        value={view}
        onChange={(v) => {
          setView(v as View)
          if (v === 'history') setPicked(null)
        }}
        items={[
          { id: 'overview', label: '概览' },
          { id: 'history', label: `评测记录${list.length ? ` ${list.length}` : ''}` },
          { id: 'bank', label: `题库${cases.data?.length ? ` ${cases.data.length}` : ''}` },
          { id: 'compare', label: '两次对比' },
        ]}
      />
      {view === 'overview' ? <EvalOverview runs={list} bankSize={cases.data?.length ?? 0} /> : null}
      {view === 'history' && picked == null ? <History runs={runs} admin={admin} onOpen={open} /> : null}
      {view === 'history' && picked != null ? <RunPanel runId={picked} admin={admin} onBack={() => setPicked(null)} onOpen={open} /> : null}
      {view === 'bank' ? <BankPanel admin={admin} onStarted={open} /> : null}
      {view === 'compare' ? <EvalCompare runs={list} admin={admin} /> : null}
    </div>
  )
}

const STATUS_FILTER = [{ value: 'all', label: '全部状态' }, ...Object.entries(RUN_STATUS).filter(([k]) => k !== 'queued').map(([value, label]) => ({ value, label }))]

/** 每次评测一行：进度、通过情况和用时实时刷新；点开看这次的指标和逐题结果。 */
function History(props: { runs: ReturnType<typeof useEvalRuns>; admin: boolean; onOpen: (id: number) => void }) {
  const models = useModels()
  const bank = useEvalCases()
  const actions = useRunActions({ onStarted: props.onOpen })
  const [status, setStatus] = useState('all')
  const modelName = (id: string) => models.data?.items.find((m) => String(m.id) === id)?.model_name ?? `模型 ${id}`
  const bankSize = bank.data?.length ?? 0
  const all = props.runs.data ?? []
  const shown = all.filter((r) => status === 'all' || r.status === status || (status === 'running' && r.status === 'queued'))
  const paged = usePaged(shown)
  const create = props.admin ? (
    <Button variant="primary" icon="plus" disabled={!bankSize} title={bankSize ? undefined : '题库是空的，先到「题库」加载基线题'} onClick={() => actions.newRun({ title: '新建评测', caseIds: [] })}>
      新建评测
    </Button>
  ) : null
  return (
    <>
      <div className="cb-filters">
        <Select
          ariaLabel="按状态筛选"
          value={status}
          onChange={(v) => {
            setStatus(v)
            paged.setPage(1)
          }}
          options={STATUS_FILTER}
        />
        <span className="cb-spacer" />
        {create}
      </div>
      {actions.dialogs}
      <ListCard label="评测记录" pager={<Pager {...paged.pager} />}>
        <ListStatus
          loading={props.runs.isPending}
          error={props.runs.error}
          onRetry={() => void props.runs.refetch()}
          what="评测记录"
          count={shown.length}
          filtered={status !== 'all'}
          onClear={() => setStatus('all')}
          cols={8}
          empty={
            <EmptyState icon="flask" title="还没有评测记录" action={create ?? undefined}>
              {props.admin ? '用整个题库跑一次评测，或到「题库」挑选部分题。' : '请管理员启动一次评测。'}
            </EmptyState>
          }
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                <th style={{ width: 64 }}>编号</th>
                <th style={{ width: 120 }}>开始时间</th>
                <th>模型与备注</th>
                <th style={{ width: 104 }}>状态</th>
                <th style={{ width: 136 }}>进度</th>
                <th style={{ width: 72 }}>通过率</th>
                <th style={{ width: 116 }}>通过 / 未通过</th>
                <th style={{ width: 108 }}>用时</th>
                {props.admin ? <th style={{ width: 150 }}><span className="cb-sr">操作</span></th> : null}
              </tr>
            </thead>
            <tbody>
              {paged.rows.map((r) => (
                <tr key={r.id} className="cb-eval-row" onClick={() => props.onOpen(r.id)}>
                  <td className="cb-m">#{r.id}</td>
                  <td className="cb-r">{formatTime(r.created_at)}</td>
                  <td>
                    <div className="cb-m" title={modelName(r.model_id)}>{modelName(r.model_id)}</div>
                    {r.note ? <div className="cb-sub" title={r.note}>{r.note}</div> : null}
                  </td>
                  <td><RunStatus status={r.status} /></td>
                  <td>
                    <div className="cb-eval-progress">
                      <div className="cb-meter" aria-hidden="true" data-level={r.status === 'canceled' || r.status === 'failed' ? 'warning' : undefined}>
                        <i style={{ width: `${r.case_count ? (r.done / r.case_count) * 100 : 0}%` }} />
                      </div>
                      <span className="cb-r">{r.done}/{r.case_count}</span>
                    </div>
                  </td>
                  <td className="cb-r">{passRate(r)}</td>
                  <td className="cb-r">{r.done ? `${r.passed} / ${r.done - r.passed}` : '—'}</td>
                  <td className="cb-r">{r.duration_ms ? formatSpan(r.duration_ms) : '—'}</td>
                  {props.admin ? (
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="cb-actions">
                        {isActive(r.status) ? <Button size="sm" variant="ghost" disabled={actions.busy} onClick={() => actions.stop(r.id)}>停止</Button> : null}
                        {canResume(r) ? <Button size="sm" variant="ghost" disabled={actions.busy} onClick={() => actions.resume(r.id)}>继续</Button> : null}
                        {!isActive(r.status) && !canResume(r) ? <Button size="sm" variant="ghost" disabled={actions.busy} onClick={() => actions.rerun(r.id)}>重新运行</Button> : null}
                        {!isActive(r.status) ? <Button size="sm" variant="ghost" onClick={() => actions.askDelete(r.id)}>删除</Button> : null}
                      </div>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>
      <div className="cb-muted">通过率只统计已完成的题；用时是各题回答时间之和，不含停止期间。点击一行查看指标和每道题的结果。</div>
    </>
  )
}

const DIFF_FILTER = [{ id: 'all', label: '全部难度' }, ...Object.entries(DIFFICULTY).map(([id, label]) => ({ id, label }))]

function BankPanel(props: { admin: boolean; onStarted: (id: number) => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const cases = useEvalCases()
  const actions = useRunActions({ onStarted: props.onStarted })
  const [category, setCategory] = useState('')
  const [difficulty, setDifficulty] = useState('all')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const all = cases.data ?? []
  const categories = [...new Set(all.map((c) => c.category))]
  const needle = search.trim().toLowerCase()
  const shown = all.filter((c) => (!category || c.category === category) && (difficulty === 'all' || c.difficulty === difficulty) && (!needle || `${c.id} ${c.question}`.toLowerCase().includes(needle)))
  const paged = usePaged(shown)
  const allShownSelected = shown.length > 0 && shown.every((c) => selected.has(c.id))
  const filtered = !!category || difficulty !== 'all' || !!needle

  const load = async () => {
    setBusy(true)
    try {
      const n = await loadEvalCases()
      await qc.invalidateQueries({ queryKey: ['eval', 'cases'] })
      toast({ tone: 'success', title: `已加载 ${formatInt(n)} 道基线题` })
    } catch (e) {
      toast({ tone: 'danger', title: '没能加载基线题库', message: e instanceof ApiError ? friendlyError(e.code).title : '请稍后重试' })
    } finally {
      setBusy(false)
    }
  }
  const loadButton = props.admin ? <Button disabled={busy} onClick={() => void load()}>加载基线题库</Button> : null

  const toggle = (id: string) =>
    setSelected((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const clear = () => {
    setCategory('')
    setDifficulty('all')
    setSearch('')
  }
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    paged.setPage(1)
  }

  return (
    <>
      <div className="cb-filters">
        <Select
          ariaLabel="按分类筛选"
          value={category}
          onChange={reset(setCategory)}
          options={[{ value: '', label: `全部分类（${all.length}）` }, ...categories.map((c) => ({ value: c, label: `${CATEGORY[c] ?? c}（${all.filter((x) => x.category === c).length}）` }))]}
        />
        <Segmented label="难度" value={difficulty} onChange={reset(setDifficulty)} items={DIFF_FILTER} />
        <div className="cb-search">
          <TextField aria-label="搜索题目" placeholder="搜索题号或问题" icon="search" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
        </div>
        <span className="cb-spacer" />
        {props.admin ? (
          <>
            {loadButton}
            <Button disabled={busy || !selected.size} onClick={() => actions.newRun({ title: `运行选中的 ${selected.size} 道题`, caseIds: [...selected] })}>
              运行选中（{selected.size}）
            </Button>
            <Button variant="primary" icon="play" disabled={busy || !all.length} onClick={() => actions.newRun({ title: '运行全部题目', caseIds: [] })}>
              运行全部
            </Button>
          </>
        ) : null}
      </div>
      {actions.dialogs}
      <ListCard label="题库" pager={<Pager {...paged.pager} />}>
        <ListStatus
          loading={cases.isPending}
          error={cases.error}
          onRetry={() => void cases.refetch()}
          what="题库"
          count={shown.length}
          filtered={filtered}
          query={needle ? search.trim() : undefined}
          onClear={clear}
          cols={6}
          empty={
            <EmptyState icon="flask" title="题库是空的" action={loadButton ?? undefined}>
              {props.admin ? '导入内置的基线题后，就可以对整个题库跑评测。' : '请管理员加载基线题库。'}
            </EmptyState>
          }
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                {props.admin ? (
                  <th style={{ width: 44 }}>
                    <input
                      type="checkbox"
                      aria-label="全选筛选结果"
                      checked={allShownSelected}
                      onChange={() =>
                        setSelected((cur) => {
                          const next = new Set(cur)
                          for (const c of shown) if (allShownSelected) next.delete(c.id)
                          else next.add(c.id)
                          return next
                        })
                      }
                    />
                  </th>
                ) : null}
                <th style={{ width: 72 }}>题号</th>
                <th>问题</th>
                <th style={{ width: 104 }}>分类</th>
                <th style={{ width: 72 }}>难度</th>
                <th style={{ width: 88 }}>期望</th>
                <th style={{ width: 144 }}>提问账号</th>
              </tr>
            </thead>
            <tbody>
              {paged.rows.map((c) => (
                <Fragment key={c.id}>
                  <tr className="cb-eval-row" onClick={() => setOpen(open === c.id ? null : c.id)}>
                    {props.admin ? (
                      <td onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" aria-label={`选择 ${c.id}`} checked={selected.has(c.id)} onChange={() => toggle(c.id)} />
                      </td>
                    ) : null}
                    <td className="cb-m">{c.id}</td>
                    <td title={c.question}>{c.question}</td>
                    <td>{CATEGORY[c.category] ?? c.category}</td>
                    <td>{DIFFICULTY[c.difficulty] ?? c.difficulty}</td>
                    <td>{EXPECT[c.expect_status] ?? c.expect_status}</td>
                    <td className="cb-m" title={c.as_user}>{c.as_user}</td>
                  </tr>
                  {open === c.id ? (
                    <tr className="cb-eval-detail">
                      <td colSpan={props.admin ? 7 : 6}>
                        <CaseDetail item={c} admin={props.admin} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>
    </>
  )
}

function CaseDetail({ item, admin }: { item: EvalCaseView; admin: boolean }) {
  return (
    <div className="cb-col">
      <div className="cb-eval-question">{item.question}</div>
      {item.history?.length ? <div className="cb-muted">前文：{item.history.map((h) => String((h as { question?: string }).question ?? '')).filter(Boolean).join(' → ')}</div> : null}
      <div>
        期望：{EXPECT[item.expect_status] ?? item.expect_status}
        {item.expect_code ? `（${friendlyError(item.expect_code).title}）` : ''}
      </div>
      {admin && item.expected_sql ? <SqlBlock sql={item.expected_sql} defaultOpen /> : null}
    </div>
  )
}
