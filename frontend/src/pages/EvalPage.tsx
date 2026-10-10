import { Fragment, useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { cancelEval, deleteEval, getEvalRun, loadEvalCases, resumeEval, startEval, useEvalCases, useEvalCompare, useEvalResults, useEvalRun, useEvalRuns, useModels } from '../api/hooks'
import type { EvalCaseView, Evaluation, Role, RunListItem } from '../api/types'
import { Badge, Button, Callout, Panel, Tabs, TextField } from '../components/basic'
import type { IconName } from '../components/Icon'
import { Dialog, DialogFooter } from '../components/Dialog'
import { SqlBlock } from '../components/chat'
import { Select } from '../components/Select'
import { ApiError, friendlyError } from '../lib/errors'
import { formatInt, formatMs, formatSpan, formatTime } from '../lib/format'

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

const EXPECT: Record<string, string> = { success: '返回结果', blocked: '拦截', clarify: '先澄清' }
const ACTUAL: Record<string, string> = { success: '返回了结果', blocked: '被拦截', clarify: '要求澄清', failed: '执行失败' }
const DIFFICULTY: Record<string, string> = { easy: '简单', medium: '中等', hard: '困难' }
const RUN_STATUS: Record<string, string> = { queued: '排队中', running: '运行中', success: '已完成', failed: '已中断', canceled: '已停止' }
const isActive = (status: string) => status === 'queued' || status === 'running'
/** 停止或中断后还有没答的题，可以从断点接着跑。 */
const canResume = (r: { status: string; done: number; case_count: number }) => (r.status === 'canceled' || r.status === 'failed') && r.done < r.case_count

function RunStatus({ status }: { status: string }) {
  const [tone, icon]: ['success' | 'danger' | 'info' | 'warning', IconName] =
    status === 'success' ? ['success', 'check'] : status === 'failed' ? ['danger', 'alert'] : status === 'canceled' ? ['warning', 'alert'] : ['info', 'loader']
  return <Badge tone={tone} icon={icon}>{RUN_STATUS[status] ?? status}</Badge>
}

interface StartPlan {
  title: string
  /** 空数组表示整个题库。 */
  caseIds: string[]
  model?: string
  note?: string
}

/** 开始前确认题数、模型和备注，并按最近一次评测的平均耗时估算要跑多久。 */
function StartDialog(props: { plan: StartPlan; onClose: () => void; onStarted: (id: number) => void }) {
  const qc = useQueryClient()
  const models = useModels()
  const runs = useEvalRuns()
  const bank = useEvalCases()
  const llms = (models.data?.items ?? []).filter((m) => m.type === 'llm' && m.enabled)
  const [modelId, setModelId] = useState(props.plan.model ?? '')
  const [note, setNote] = useState(props.plan.note ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const model = llms.some((m) => String(m.id) === modelId) ? modelId : String((llms.find((m) => m.is_default) ?? llms[0])?.id ?? '')
  const total = bank.data?.length ?? 0
  const count = props.plan.caseIds.length || total
  const sample = (runs.data ?? []).find((r) => r.done > 0 && r.duration_ms > 0)
  const perCase = sample ? sample.duration_ms / sample.done : null

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const run = await startEval({ model_id: model, case_ids: props.plan.caseIds, note: note.trim() })
      await qc.invalidateQueries({ queryKey: ['eval', 'runs'] })
      props.onStarted(run.id)
    } catch (e) {
      setError(e instanceof ApiError ? friendlyError(e.code).title : '没能开始评测，请稍后重试')
      setBusy(false)
    }
  }

  return (
    <Dialog title={props.plan.title} onClose={props.onClose}>
      <div className="cb-col">
        <div>
          将运行 <b>{formatInt(count)}</b> 道题
          {perCase ? `，预计约 ${formatSpan(perCase * count)}（按最近一次评测平均每题 ${formatMs(Math.round(perCase))} 估算）` : ''}。
          运行中可以随时停止，之后能从断点接着跑。
        </div>
        {count < total ? <Callout tone="info">只跑部分题时，安全门槛不会判定为通过，结果不能作为发布依据。</Callout> : null}
        <div className="cb-field">
          <label htmlFor="eval-start-model">模型</label>
          <Select id="eval-start-model" value={model} onChange={setModelId} options={llms.map((m) => ({ value: String(m.id), label: m.model_name }))} />
        </div>
        <TextField label="备注（可选）" hint="写下这次评测在验证什么，方便以后对比。" placeholder="例如：提示词 v8，或换模型对比" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
        {models.data && !llms.length ? <Callout tone="warning">还没有可用的对话模型，请先在「模型配置」添加。</Callout> : null}
        {error ? <Callout tone="danger">{error}</Callout> : null}
      </div>
      <DialogFooter>
        <Button onClick={props.onClose}>取消</Button>
        <Button variant="primary" icon="play" disabled={busy || !model || !count} onClick={() => void start()}>
          开始评测
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

/**
 * 一次评测的所有操作：停止（保留已完成的题）、继续（从断点接着跑）、重新运行（同样的题开一次新评测）、
 * 删除（连同逐题结果，不可恢复，需要确认）。停止可以继续，所以不需要确认。
 */
function useRunActions(opts: { onDeleted?: (id: number) => void; onStarted?: (id: number) => void } = {}) {
  const qc = useQueryClient()
  const [deleting, setDeleting] = useState<number | null>(null)
  const [plan, setPlan] = useState<StartPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await qc.invalidateQueries({ queryKey: ['eval'] })
      return true
    } catch (e) {
      setError(e instanceof ApiError ? friendlyError(e.code).title : '操作失败，请稍后重试')
      return false
    } finally {
      setBusy(false)
    }
  }
  const stop = (id: number) => void act(() => cancelEval(id))
  const resume = (id: number) => void act(() => resumeEval(id))
  /** 用这次评测的题和模型开一次新评测；只给 caseIds 时只跑这些题（比如未通过的）。 */
  const rerun = (id: number, caseIds?: string[]) =>
    void act(async () => {
      const run = await getEvalRun(id)
      setPlan({
        title: caseIds ? `重跑评测 #${id} 未通过的 ${caseIds.length} 道题` : `重新运行评测 #${id}`,
        caseIds: caseIds ?? run.case_ids ?? [],
        model: run.model_id,
        note: caseIds ? `重跑 #${id} 未通过的题` : run.note ? `${run.note}（重跑 #${id}）` : `重跑 #${id}`,
      })
    })
  const dialogs = (
    <>
      {deleting != null ? (
        <Dialog title={`删除评测 #${deleting}`} onClose={() => setDeleting(null)}>
          <div>删除后这次评测的指标和逐题结果都会移除，不能恢复。</div>
          {error ? <Callout tone="danger">{error}</Callout> : null}
          <DialogFooter>
            <Button onClick={() => setDeleting(null)}>取消</Button>
            <Button
              variant="danger"
              disabled={busy}
              onClick={() =>
                void act(() => deleteEval(deleting)).then((ok) => {
                  if (!ok) return
                  setDeleting(null)
                  opts.onDeleted?.(deleting)
                })
              }
            >
              删除评测
            </Button>
          </DialogFooter>
        </Dialog>
      ) : null}
      {plan ? (
        <StartDialog
          plan={plan}
          onClose={() => setPlan(null)}
          onStarted={(id) => {
            setPlan(null)
            opts.onStarted?.(id)
          }}
        />
      ) : null}
    </>
  )
  return { stop, resume, rerun, newRun: setPlan, askDelete: setDeleting, busy, error: deleting == null ? error : null, dialogs }
}

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

const pct = (v: unknown) => (typeof v === 'number' ? `${Math.round(v * 1000) / 10}%` : '—')
const num = (v: unknown) => (typeof v === 'number' ? v : undefined)
const passRate = (r: { done: number; passed: number }) => (r.done ? pct(r.passed / r.done) : '—')
const runLabel = (r: RunListItem) =>
  `#${r.id} · ${formatTime(r.created_at)}${r.note ? ` · ${r.note}` : ''} · ${RUN_STATUS[r.status] ?? r.status} · ${r.passed}/${r.done} 通过`

export function EvalPage({ role }: { role: Role }) {
  const admin = role === 'admin'
  const [view, setView] = useState('bank')
  const [picked, setPicked] = useState<number | null>(null)
  const runs = useEvalRuns()
  const list = runs.data ?? []

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
          setView(v)
          if (v === 'history') setPicked(null)
        }}
        items={[
          { id: 'bank', label: '题库' },
          { id: 'history', label: `评测记录${list.length ? ` ${list.length}` : ''}` },
          { id: 'compare', label: '两次对比' },
        ]}
      />
      {view === 'bank' ? <BankPanel admin={admin} onStarted={open} /> : null}
      {view === 'history' && picked == null ? <History runs={list} loading={runs.isLoading} admin={admin} onOpen={open} /> : null}
      {view === 'history' && picked != null ? <RunPanel runId={picked} admin={admin} onBack={() => setPicked(null)} onOpen={open} /> : null}
      {view === 'compare' ? (
        <Panel title="两次对比">
          <Compare runs={list} />
        </Panel>
      ) : null}
    </div>
  )
}

/** 每次评测一行：进度、通过情况和用时实时刷新；点开看这次的指标和逐题结果。 */
function History(props: { runs: RunListItem[]; loading: boolean; admin: boolean; onOpen: (id: number) => void }) {
  const models = useModels()
  const bank = useEvalCases()
  const actions = useRunActions({ onStarted: props.onOpen })
  const modelName = (id: string) => models.data?.items.find((m) => String(m.id) === id)?.model_name ?? `模型 ${id}`
  const bankSize = bank.data?.length ?? 0
  return (
    <Panel
      title="评测记录"
      actions={
        props.admin ? (
          <Button variant="primary" icon="play" disabled={!bankSize} title={bankSize ? undefined : '题库是空的，先到「题库」加载基线题'} onClick={() => actions.newRun({ title: '新建评测', caseIds: [] })}>
            新建评测
          </Button>
        ) : undefined
      }
    >
      {actions.error ? <Callout tone="danger">{actions.error}</Callout> : null}
      {actions.dialogs}
      {props.loading ? <div className="cb-muted">加载中…</div> : null}
      {!props.loading && !props.runs.length ? (
        <div className="cb-muted">还没有评测记录。{props.admin ? '点「新建评测」用整个题库跑一次，或到「题库」挑选部分题。' : '请管理员启动一次评测。'}</div>
      ) : null}
      {props.runs.length ? (
        <>
          <table className="cb-data cb-eval-history">
            <thead>
              <tr>
                <th style={{ width: 56 }}>编号</th>
                <th style={{ width: 120 }}>开始时间</th>
                <th>模型与备注</th>
                <th style={{ width: 96 }}>状态</th>
                <th style={{ width: 150 }}>进度</th>
                <th className="cb-r" style={{ width: 56 }}>通过</th>
                <th className="cb-r" style={{ width: 64 }}>未通过</th>
                <th className="cb-r" style={{ width: 72 }}>通过率</th>
                <th className="cb-r" style={{ width: 96 }}>用时</th>
                {props.admin ? <th className="cb-r" style={{ width: 150 }}>操作</th> : null}
              </tr>
            </thead>
            <tbody>
              {props.runs.map((r) => (
                <tr key={r.id} className="cb-eval-row" onClick={() => props.onOpen(r.id)}>
                  <td className="cb-mono">#{r.id}</td>
                  <td className="cb-num">{formatTime(r.created_at)}</td>
                  <td>
                    <div className="cb-mono">{modelName(r.model_id)}</div>
                    {r.note ? <div className="cb-muted cb-eval-note" title={r.note}>{r.note}</div> : null}
                  </td>
                  <td>
                    <RunStatus status={r.status} />
                  </td>
                  <td>
                    <div className="cb-eval-progress">
                      <div className="cb-meter" aria-hidden="true" data-level={r.status === 'canceled' || r.status === 'failed' ? 'warning' : undefined}>
                        <i style={{ width: `${r.case_count ? (r.done / r.case_count) * 100 : 0}%` }} />
                      </div>
                      <span className="cb-num">{r.done}/{r.case_count}</span>
                    </div>
                  </td>
                  <td className="cb-r cb-num">{r.done ? r.passed : '—'}</td>
                  <td className="cb-r cb-num">{r.done ? r.done - r.passed : '—'}</td>
                  <td className="cb-r cb-num">{passRate(r)}</td>
                  <td className="cb-r cb-num">{r.duration_ms ? formatSpan(r.duration_ms) : '—'}</td>
                  {props.admin ? (
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="cb-eval-actions">
                        {isActive(r.status) ? (
                          <Button size="sm" variant="ghost" disabled={actions.busy} onClick={() => actions.stop(r.id)}>停止</Button>
                        ) : null}
                        {canResume(r) ? (
                          <Button size="sm" variant="ghost" disabled={actions.busy} onClick={() => actions.resume(r.id)}>继续</Button>
                        ) : null}
                        {!isActive(r.status) && !canResume(r) ? (
                          <Button size="sm" variant="ghost" disabled={actions.busy} onClick={() => actions.rerun(r.id)}>重新运行</Button>
                        ) : null}
                        {!isActive(r.status) ? (
                          <Button size="sm" variant="ghost" onClick={() => actions.askDelete(r.id)}>删除</Button>
                        ) : null}
                      </div>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="cb-muted">通过率只统计已完成的题；用时是各题回答时间之和，不含停止期间。点击一行查看指标和每道题的结果。</div>
        </>
      ) : null}
    </Panel>
  )
}

function BankPanel(props: { admin: boolean; onStarted: (id: number) => void }) {
  const qc = useQueryClient()
  const cases = useEvalCases()
  const actions = useRunActions({ onStarted: props.onStarted })
  const [category, setCategory] = useState('')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: 'success' | 'danger'; text: string } | null>(null)
  const all = cases.data ?? []
  const categories = [...new Set(all.map((c) => c.category))]
  const shown = all.filter((c) => (!category || c.category === category) && (!search || `${c.id} ${c.question}`.toLowerCase().includes(search.toLowerCase())))
  const allShownSelected = shown.length > 0 && shown.every((c) => selected.has(c.id))

  const guard = async (fn: () => Promise<string | null>) => {
    setBusy(true)
    setMessage(null)
    try {
      const text = await fn()
      if (text) setMessage({ tone: 'success', text })
    } catch (e) {
      setMessage({ tone: 'danger', text: e instanceof ApiError ? friendlyError(e.code).title : '操作失败，请稍后重试' })
    } finally {
      setBusy(false)
    }
  }

  const toggle = (id: string) =>
    setSelected((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <Panel
      title={`题库（${all.length} 题）`}
      actions={
        props.admin ? (
          <div className="cb-row">
            <Button
              disabled={busy}
              onClick={() =>
                void guard(async () => {
                  const n = await loadEvalCases()
                  await qc.invalidateQueries({ queryKey: ['eval', 'cases'] })
                  return `已加载 ${formatInt(n)} 道基线题。`
                })
              }
            >
              加载基线题库
            </Button>
            <Button disabled={busy || !selected.size} onClick={() => actions.newRun({ title: `运行选中的 ${selected.size} 道题`, caseIds: [...selected] })}>
              运行选中（{selected.size}）
            </Button>
            <Button variant="primary" icon="play" disabled={busy || !all.length} onClick={() => actions.newRun({ title: '运行全部题目', caseIds: [] })}>
              运行全部
            </Button>
          </div>
        ) : undefined
      }
    >
      {message ? <Callout tone={message.tone}>{message.text}</Callout> : null}
      {actions.dialogs}
      {cases.data && !all.length ? <div className="cb-muted">题库是空的。{props.admin ? '点「加载基线题库」导入内置的基线题。' : '请管理员加载基线题库。'}</div> : null}
      {all.length ? (
        <>
          <div className="cb-toolbar">
            <Tabs label="分类" value={category} onChange={setCategory} items={[{ id: '', label: `全部 ${all.length}` }, ...categories.map((c) => ({ id: c, label: `${CATEGORY[c] ?? c} ${all.filter((x) => x.category === c).length}` }))]} />
            <span className="cb-spacer" />
            <TextField aria-label="搜索题目" placeholder="搜索题号或问题" icon="search" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <table className="cb-data cb-eval-table">
            <thead>
              <tr>
                {props.admin ? (
                  <th style={{ width: 32 }}>
                    <input
                      type="checkbox"
                      aria-label="全选当前列表"
                      checked={allShownSelected}
                      onChange={() => setSelected((cur) => {
                        const next = new Set(cur)
                        for (const c of shown) if (allShownSelected) next.delete(c.id)
                        else next.add(c.id)
                        return next
                      })}
                    />
                  </th>
                ) : null}
                <th style={{ width: 64 }}>题号</th>
                <th>问题</th>
                <th style={{ width: 96 }}>分类</th>
                <th style={{ width: 64 }}>难度</th>
                <th style={{ width: 84 }}>期望</th>
                <th style={{ width: 110 }}>提问账号</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((c) => (
                <Fragment key={c.id}>
                  <tr className="cb-eval-row" onClick={() => setOpen(open === c.id ? null : c.id)}>
                    {props.admin ? (
                      <td onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" aria-label={`选择 ${c.id}`} checked={selected.has(c.id)} onChange={() => toggle(c.id)} />
                      </td>
                    ) : null}
                    <td className="cb-mono">{c.id}</td>
                    <td className="cb-wrap">{c.question}</td>
                    <td>{CATEGORY[c.category] ?? c.category}</td>
                    <td>{DIFFICULTY[c.difficulty] ?? c.difficulty}</td>
                    <td>{EXPECT[c.expect_status] ?? c.expect_status}</td>
                    <td className="cb-muted">{c.as_user}</td>
                  </tr>
                  {open === c.id ? (
                    <tr className="cb-eval-detail">
                      <td colSpan={props.admin ? 7 : 6}>
                        <CaseDetail item={c} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </Panel>
  )
}

function CaseDetail({ item }: { item: EvalCaseView }) {
  return (
    <div className="cb-col">
      {item.history?.length ? <div className="cb-muted">前文：{item.history.map((h) => String((h as { question?: string }).question ?? '')).filter(Boolean).join(' → ')}</div> : null}
      <div>
        期望：{EXPECT[item.expect_status] ?? item.expect_status}
        {item.expect_code ? `（${friendlyError(item.expect_code).title}）` : ''}
      </div>
      {item.expected_sql ? <SqlBlock sql={item.expected_sql} defaultOpen /> : null}
    </div>
  )
}

function RunPanel(props: { runId: number; admin: boolean; onBack: () => void; onOpen: (id: number) => void }) {
  const run = useEvalRun(props.runId)
  const results = useEvalResults(props.runId)
  const actions = useRunActions({ onDeleted: props.onBack, onStarted: props.onOpen })
  const data = run.data
  const failedIds = (results.data ?? []).filter((r) => !r.passed).map((r) => r.case_id)
  const active = !!data && isActive(data.status)
  return (
    <Panel
      eyebrow={data?.note || undefined}
      title={`评测 #${props.runId}`}
      actions={
        <div className="cb-row">
          {props.admin && data && active ? (
            <Button variant="danger" disabled={actions.busy} onClick={() => actions.stop(props.runId)}>停止评测</Button>
          ) : null}
          {props.admin && data && canResume(data) ? (
            <Button variant="primary" icon="play" disabled={actions.busy} onClick={() => actions.resume(props.runId)}>继续评测</Button>
          ) : null}
          {props.admin && data && !active && failedIds.length ? (
            <Button disabled={actions.busy} onClick={() => actions.rerun(props.runId, failedIds)}>重跑未通过的题（{failedIds.length}）</Button>
          ) : null}
          {props.admin && data && !active ? (
            <Button disabled={actions.busy} onClick={() => actions.rerun(props.runId)}>重新运行</Button>
          ) : null}
          {props.admin && data && !active ? <Button onClick={() => actions.askDelete(props.runId)}>删除评测</Button> : null}
          <Button onClick={props.onBack}>返回评测记录</Button>
        </div>
      }
    >
      {actions.error ? <Callout tone="danger">{actions.error}</Callout> : null}
      {actions.dialogs}
      <RunView runId={props.runId} admin={props.admin} />
    </Panel>
  )
}

function RunView({ runId, admin }: { runId: number; admin: boolean }) {
  const run = useEvalRun(runId)
  const running = !!run.data && ['queued', 'running'].includes(run.data.status)
  const results = useEvalResults(runId, running)
  const models = useModels()
  const [filter, setFilter] = useState('all')
  const [category, setCategory] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const rows = useMemo(() => results.data ?? [], [results.data])
  const categories = useMemo(() => [...new Set(rows.map((r) => r.category))], [rows])
  if (!run.data) return <div className="cb-muted">加载中…</div>
  const s = (run.data.summary ?? {}) as Record<string, unknown>
  const gate = s.safety_gate_passed
  const failed = rows.filter((r) => !r.passed)
  const passedCount = rows.length - failed.length
  const shown = rows.filter((r) => (filter === 'all' || (filter === 'passed') === r.passed) && (!category || r.category === category))
  const modelName = models.data?.items.find((m) => String(m.id) === run.data?.model_id)?.model_name ?? `模型 ${run.data.model_id}`
  const total = run.data.case_count || rows.length
  const done = Math.max(run.data.done, rows.length)

  return (
    <div className="cb-col">
      <div className="cb-row">
        <RunStatus status={run.data.status} />
        <span className="cb-mono">{modelName}</span>
        {run.data.created_at ? <span className="cb-muted">{formatTime(run.data.created_at)}</span> : null}
        {!running && done < total ? (
          <Badge tone="neutral" icon="shield">安全门槛未判定（题没跑完）</Badge>
        ) : gate === true ? (
          <Badge tone="success" icon="shield">安全门槛通过</Badge>
        ) : gate === false ? (
          <Badge tone="danger" icon="shield">安全门槛未通过</Badge>
        ) : null}
      </div>
      {run.data.status === 'canceled' && done < total ? (
        <Callout tone="warning" title="评测已停止">
          停止前完成了 {done}/{total} 题，下面的指标只统计已完成的题。{admin ? `点「继续评测」接着跑剩下的 ${total - done} 题。` : ''}
        </Callout>
      ) : null}
      {run.data.status === 'failed' ? (
        <Callout tone="danger" title="评测中断">
          运行到第 {done + 1} 题时中断，可能是服务重启或模型服务不可用。已完成的 {done} 题保留。
          {admin && done < total ? `点「继续评测」接着跑剩下的 ${total - done} 题。` : ''}
        </Callout>
      ) : null}
      {running ? (
        <div className="cb-meter-row">
          <div className="cb-meter" role="progressbar" aria-valuenow={done} aria-valuemin={0} aria-valuemax={total} aria-label="评测进度">
            <i style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
          </div>
          <span className="cb-meter-label">
            已完成 {done}/{total} 题
          </span>
        </div>
      ) : null}
      {!running && looksUnprovisioned(rows) ? <Callout tone="warning" title="评测环境未准备好">所有题都在调用模型之前就执行失败。评测需要先准备好测试团队、用户、测试库和对应的绑定配置，否则结果不代表模型质量。</Callout> : null}
      <div className="cb-grid-stats">
        {done < total ? <Stat value={`${formatInt(done)} / ${formatInt(total)}`} label="已完成 / 总题数" /> : <Stat value={formatInt(total)} label="总题数" />}
        <Stat value={formatInt(passedCount)} label="通过" tone="success" />
        <Stat value={formatInt(failed.length)} label="未通过" tone={failed.length ? 'danger' : undefined} />
        <Stat value={done ? pct(passedCount / done) : '—'} label={running ? '当前通过率' : '通过率'} />
        {!running && num(s.total) !== undefined ? (
          <>
            <Stat value={pct(s.first_pass_rate)} label="一次通过率" />
            <Stat value={pct(s.blocked_pass_rate)} label="安全题拦截率" />
            <Stat value={formatMs(Math.round(num(s.average_duration_ms) ?? 0))} label="平均耗时" />
            <Stat value={`${formatInt(num(s.tokens_in) ?? 0)} / ${formatInt(num(s.tokens_out) ?? 0)}`} label="Token 输入 / 输出" />
          </>
        ) : null}
      </div>
      {rows.length ? (
        <>
          <div className="cb-toolbar">
            <Tabs
              label="结果筛选"
              value={filter}
              onChange={setFilter}
              items={[
                { id: 'all', label: `全部 ${rows.length}` },
                { id: 'failed', label: `未通过 ${failed.length}` },
                { id: 'passed', label: `通过 ${passedCount}` },
              ]}
            />
            <span className="cb-spacer" />
            <Select
              ariaLabel="按分类筛选"
              value={category}
              onChange={setCategory}
              options={[{ value: '', label: '全部分类' }, ...categories.map((c) => ({ value: c, label: `${CATEGORY[c] ?? c}（${rows.filter((r) => r.category === c && r.passed).length}/${rows.filter((r) => r.category === c).length}）` }))]}
            />
          </div>
          <div className="cb-table-wrap">
            <table className="cb-data cb-eval-table">
              <thead>
                <tr>
                  <th style={{ width: 56 }}>题号</th>
                  <th style={{ minWidth: 220 }}>问题</th>
                  <th style={{ width: 88 }}>分类</th>
                  <th style={{ width: 56 }}>难度</th>
                  <th style={{ width: 110 }}>提问账号</th>
                  <th style={{ width: 80 }}>期望</th>
                  <th style={{ width: 88 }}>实际</th>
                  <th style={{ width: 88 }}>结果</th>
                  <th style={{ minWidth: 160 }}>原因</th>
                  <th className="cb-r" style={{ width: 56 }}>纠错</th>
                  <th className="cb-r" style={{ width: 72 }}>耗时</th>
                  <th className="cb-r" style={{ width: 120 }}>Token 入 / 出</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.case_id} className="cb-eval-row" onClick={() => setOpen(r.case_id)}>
                    <td className="cb-mono">{r.case_id}</td>
                    <td className="cb-wrap">{r.case?.question ?? <span className="cb-muted">—</span>}</td>
                    <td>{CATEGORY[r.category] ?? r.category}</td>
                    <td>{r.case ? DIFFICULTY[r.case.difficulty] ?? r.case.difficulty : '—'}</td>
                    <td className="cb-muted">{r.case?.as_user ?? '—'}</td>
                    <td>{r.case ? EXPECT[r.case.expect_status] ?? r.case.expect_status : '—'}</td>
                    <td>{r.actual_status ? ACTUAL[r.actual_status] ?? r.actual_status : '—'}</td>
                    <td>{r.passed ? <Badge tone="success" icon="check">通过</Badge> : <Badge tone="danger" icon="x">未通过</Badge>}</td>
                    <td className={r.passed ? 'cb-wrap cb-muted' : 'cb-wrap'}>{r.passed ? '—' : reasonText(r)}</td>
                    <td className="cb-r cb-num">{r.retries || '—'}</td>
                    <td className="cb-r cb-num">{formatMs(r.duration_ms)}</td>
                    <td className="cb-r cb-num">{formatInt(r.tokens_in)} / {formatInt(r.tokens_out)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="cb-muted">点击一行查看期望与实际的对比。</div>
          {open != null && shown.some((r) => r.case_id === open) ? (
            <ResultDialog results={shown} caseId={open} admin={admin} onPick={setOpen} onClose={() => setOpen(null)} />
          ) : null}
        </>
      ) : running ? <div className="cb-muted">第一题正在运行…</div> : null}
    </div>
  )
}

const MODE: Record<string, string> = { unordered: '不计行顺序', ordered: '按行顺序', scalar: '单个值' }

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

/** 一道题的期望与实际：先看比对结论和差异行，再左右对照两边的结果和 SQL。←/→ 切换题目。 */
function ResultDialog(props: { results: Evaluation[]; caseId: string; admin: boolean; onPick: (id: string) => void; onClose: () => void }) {
  const { results, caseId, onPick } = props
  const index = results.findIndex((x) => x.case_id === caseId)
  const r = results[index]
  const prev = results[index - 1]?.case_id
  const next = results[index + 1]?.case_id
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && e.target.closest('input, textarea, select')) return
      if (e.key === 'ArrowLeft' && prev) onPick(prev)
      if (e.key === 'ArrowRight' && next) onPick(next)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [prev, next, onPick])
  if (!r) return null
  const expect = r.case?.expect_status ?? 'success'
  const mode = r.case?.result_mode ?? 'unordered'
  const summary = compareSummary(r)
  const compared = r.expected_row_count != null
  const resultCase = expect === 'success' && r.actual_status === 'success'
  return (
    <Dialog title={`${r.case_id} 期望与实际对比`} size="wide" onClose={props.onClose}>
      <div className="cb-col cb-eval-dialog">
        <div className="cb-eval-question">{r.case?.question ?? '—'}</div>
        <div className="cb-row" style={{ flexWrap: 'wrap' }}>
          {r.passed ? <Badge tone="success" icon="check">通过</Badge> : <Badge tone="danger" icon="x">未通过</Badge>}
          {r.passed ? null : <span>{reasonText(r)}</span>}
          <span className="cb-spacer" />
          <span className="cb-muted">
            {[CATEGORY[r.category] ?? r.category, r.case ? DIFFICULTY[r.case.difficulty] ?? r.case.difficulty : null, r.case?.as_user, formatMs(r.duration_ms), r.retries ? `自动纠错 ${r.retries} 次` : null, `Token ${formatInt(r.tokens_in)} / ${formatInt(r.tokens_out)}`].filter(Boolean).join(' · ')}
          </span>
        </div>
        {resultCase && compared ? (
          <>
            <div className="cb-grid-stats">
              <Stat value={formatInt(r.expected_row_count ?? 0)} label="期望行数" />
              <Stat value={formatInt(r.actual_row_count ?? 0)} label="实际行数" />
              <Stat value={r.matched_rows == null ? '—' : formatInt(r.matched_rows)} label="一致行数" tone={summary?.tone === 'success' ? 'success' : summary?.tone === 'danger' ? 'danger' : undefined} />
              <Stat value={MODE[mode] ?? mode} label="比对方式" />
            </div>
            {summary ? (
              <Callout tone={summary.tone} title={summary.text}>
                比对时{mode === 'unordered' ? '不看行的先后顺序，' : ''}不看列名和列的先后，数值相差不超过 0.000001 视为相等。
              </Callout>
            ) : null}
          </>
        ) : resultCase && !r.case?.expect_empty ? (
          <Callout tone="info">这条结果记录于比对明细上线之前，两边各只保存了前 5 行，行顺序也可能不同。重跑这道题可以看到完整的比对。</Callout>
        ) : null}
        <div className="cb-eval-compare">
          <section>
            <div className="cb-eyebrow">期望</div>
            <div>
              {EXPECT[expect] ?? expect}
              {r.case?.expect_code ? `（${friendlyError(r.case.expect_code).title}）` : ''}
              {r.case?.expect_empty ? '（结果应为空）' : ''}
              {r.case?.require_assumption ? '，并说明按哪种理解（先澄清也算通过）' : ''}
              {r.case?.accept_safe_answer ? '（安全地回答、不执行越权内容也算通过）' : ''}
            </div>
            {r.case?.expected_clarification_options?.length ? <Options items={r.case.expected_clarification_options} /> : null}
            {r.expected_rows?.length ? <RowsCaption rows={r.expected_rows} total={r.expected_row_count} sorted={compared && mode === 'unordered'} /> : null}
            {props.admin && r.case?.expected_sql ? <SqlBlock sql={r.case.expected_sql} /> : null}
          </section>
          <section>
            <div className="cb-eyebrow">实际</div>
            <div>
              {r.actual_status ? ACTUAL[r.actual_status] ?? r.actual_status : '没有返回'}
              {r.blocked_code ? `（${friendlyError(r.blocked_code).title}）` : ''}
              {r.error ? `（${friendlyError(r.error).title}）` : ''}
            </div>
            {r.message ? <div className="cb-eval-answer">{r.message}</div> : null}
            {r.assumption ? <div className="cb-muted">按哪种理解：{r.assumption}</div> : null}
            {r.clarification_options?.length ? <Options items={r.clarification_options} /> : null}
            {r.actual_rows?.length ? <RowsCaption rows={r.actual_rows} total={r.actual_row_count} sorted={compared && mode === 'unordered'} /> : null}
            {props.admin && r.actual_sql ? <SqlBlock sql={r.actual_sql} rejected={r.actual_status === 'blocked'} /> : null}
          </section>
        </div>
      </div>
      <DialogFooter left={<span className="cb-muted">第 {index + 1} / {results.length} 题 · ←/→ 切换</span>}>
        <Button variant="ghost" disabled={!prev} onClick={() => prev && onPick(prev)}>上一题</Button>
        <Button variant="ghost" disabled={!next} onClick={() => next && onPick(next)}>下一题</Button>
        <Button onClick={props.onClose}>关闭</Button>
      </DialogFooter>
    </Dialog>
  )
}

function Options({ items }: { items: string[] }) {
  return (
    <div className="cb-row" style={{ flexWrap: 'wrap' }}>
      {items.map((c) => <Badge key={c} outline>{c}</Badge>)}
    </div>
  )
}

function RowsCaption(props: { rows: Record<string, unknown>[]; total?: number | null; sorted: boolean }) {
  const shown = props.rows.length
  const caption = props.total == null ? `前 ${shown} 行` : props.total > shown ? `共 ${formatInt(props.total)} 行，显示前 ${shown} 行` : `共 ${formatInt(props.total)} 行`
  return (
    <>
      <MiniRows rows={props.rows} />
      <div className="cb-muted">{caption}{props.sorted && props.total != null && props.total > shown ? '（两边按相同顺序排列）' : ''}</div>
    </>
  )
}

/** 前 5 行，足够看出结果哪里不一样。 */
function MiniRows({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = Object.keys(rows[0] ?? {})
  return (
    <div className="cb-table-wrap">
      <table className="cb-data">
        <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>{columns.map((c) => <td key={c} className="cb-num">{row[c] == null ? '—' : String(row[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Stat(props: { value: string; label: string; tone?: 'success' | 'danger' }) {
  return (
    <div className={props.tone ? `cb-stat cb-stat-${props.tone}` : 'cb-stat'}>
      <b>{props.value}</b>
      <span>{props.label}</span>
    </div>
  )
}

function Compare({ runs }: { runs: RunListItem[] }) {
  const [before, setBefore] = useState<number | null>(runs[1]?.id ?? null)
  const [after, setAfter] = useState<number | null>(runs[0]?.id ?? null)
  const cmp = useEvalCompare(before, after)
  const pick = (label: string, value: number | null, set: (n: number) => void) => (
    <div className="cb-field">
      <label htmlFor={`cmp-${label}`}>{label}</label>
      <Select id={`cmp-${label}`} value={value} onChange={set} options={runs.map((r) => ({ value: r.id, label: runLabel(r) }))} />
    </div>
  )
  if (runs.length < 2) return <div className="cb-muted">至少需要两次运行才能对比。</div>
  const list = (title: string, items: string[] | undefined, tone: 'danger' | 'success' | 'neutral') => (
    <div className="cb-row">
      <span style={{ width: 'var(--sidebar-width)' }}>{title}（{items?.length ?? 0}）</span>
      {(items ?? []).map((c) => <Badge key={c} tone={tone} mono>{c}</Badge>)}
    </div>
  )
  return (
    <div className="cb-col">
      <div className="cb-row">{pick('基线', before, setBefore)}{pick('对比', after, setAfter)}</div>
      {before === after ? <Callout tone="warning">请选择两次不同的运行。</Callout> : null}
      {cmp.error instanceof ApiError ? <Callout tone="danger">{cmp.error.message}</Callout> : null}
      {cmp.data ? (
        <>
          {list('新增失败', cmp.data.new_failures, 'danger')}
          {list('已修复', cmp.data.fixed, 'success')}
          {list('新增题', cmp.data.added, 'neutral')}
          {list('移除题', cmp.data.removed, 'neutral')}
        </>
      ) : null}
    </div>
  )
}
