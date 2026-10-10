import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { cancelEval, deleteEval, getEvalRun, resumeEval, startEval, useEvalCases, useEvalResults, useEvalRun, useEvalRuns, useModels } from '../api/hooks'
import type { Evaluation } from '../api/types'
import { Badge, Button, Callout, Tabs, TextField } from './basic'
import type { IconName } from './Icon'
import { ConfirmDialog, Dialog, DialogFooter } from './Dialog'
import { SqlBlock } from './chat'
import { ListCard, ListStatus } from './ListStates'
import { Pager, usePaged } from './Pager'
import { Select } from './Select'
import { StatCard, Stats } from './StatCard'
import { useToast } from './Toast'
import { ApiError, friendlyError } from '../lib/errors'
import { formatInt, formatMs, formatSpan, formatTime } from '../lib/format'
import { ACTUAL, CATEGORY, DIFFICULTY, EXPECT, MODE, RUN_STATUS, canResume, compareSummary, isActive, looksUnprovisioned, num, pct, reasonText } from '../lib/evalText'

export function RunStatus({ status }: { status: string }) {
  const [tone, icon]: ['success' | 'danger' | 'info' | 'warning', IconName] =
    status === 'success' ? ['success', 'check'] : status === 'failed' ? ['danger', 'alert'] : status === 'canceled' ? ['warning', 'alert'] : ['info', 'loader']
  return <Badge tone={tone} icon={icon}>{RUN_STATUS[status] ?? status}</Badge>
}
export interface StartPlan {
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
export function useRunActions(opts: { onDeleted?: (id: number) => void; onStarted?: (id: number) => void } = {}) {
  const qc = useQueryClient()
  const toast = useToast()
  const [deleting, setDeleting] = useState<number | null>(null)
  const [plan, setPlan] = useState<StartPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      await qc.invalidateQueries({ queryKey: ['eval'] })
      return true
    } catch (e) {
      toast({ tone: 'danger', title: '操作失败', message: e instanceof ApiError ? friendlyError(e.code).title : '请稍后重试' })
      return false
    } finally {
      setBusy(false)
    }
  }
  const stop = (id: number) => void act(() => cancelEval(id)).then((ok) => ok && toast({ tone: 'info', title: `评测 #${id} 已停止`, message: '已完成的题保留，可以随时继续。' }))
  const resume = (id: number) => void act(() => resumeEval(id)).then((ok) => ok && toast({ tone: 'info', title: `评测 #${id} 继续运行`, message: '从断点接着跑剩下的题。' }))
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
        <ConfirmDialog
          title={`删除评测 #${deleting}`}
          confirmLabel="删除评测"
          danger
          busy={busy}
          onClose={() => setDeleting(null)}
          onConfirm={() =>
            void act(() => deleteEval(deleting)).then((ok) => {
              if (!ok) return
              toast({ tone: 'success', title: `评测 #${deleting} 已删除` })
              setDeleting(null)
              opts.onDeleted?.(deleting)
            })
          }
        >
          删除后这次评测的指标和逐题结果都会移除，不能恢复。
        </ConfirmDialog>
      ) : null}
      {plan ? (
        <StartDialog
          plan={plan}
          onClose={() => setPlan(null)}
          onStarted={(id) => {
            setPlan(null)
            toast({ tone: 'info', title: `评测 #${id} 已开始`, message: '跑完会通知你，期间可以离开这个页面。' })
            opts.onStarted?.(id)
          }}
        />
      ) : null}
    </>
  )
  return { stop, resume, rerun, newRun: setPlan, askDelete: setDeleting, busy, dialogs }
}

export function RunPanel(props: { runId: number; admin: boolean; onBack: () => void; onOpen: (id: number) => void }) {
  const run = useEvalRun(props.runId)
  const results = useEvalResults(props.runId)
  const actions = useRunActions({ onDeleted: props.onBack, onStarted: props.onOpen })
  const data = run.data
  const failedIds = (results.data ?? []).filter((r) => !r.passed).map((r) => r.case_id)
  const active = !!data && isActive(data.status)
  return (
    <div className="cb-col">
      <div className="cb-filters">
        <Button variant="ghost" icon="chevronLeft" onClick={props.onBack}>返回评测记录</Button>
        <h3 className="cb-h3">评测 #{props.runId}</h3>
        {data?.note ? <span className="cb-muted">{data.note}</span> : null}
        <span className="cb-spacer" />
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
        {props.admin && data && !active ? <Button variant="danger" onClick={() => actions.askDelete(props.runId)}>删除评测</Button> : null}
      </div>
      {actions.dialogs}
      <RunView runId={props.runId} admin={props.admin} />
    </div>
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
  const shownAll = useMemo(() => rows.filter((r) => (filter === 'all' || (filter === 'passed') === r.passed) && (!category || r.category === category)), [rows, filter, category])
  const paged = usePaged(shownAll)
  if (!run.data) return <div className="cb-muted">加载中…</div>
  const s = (run.data.summary ?? {}) as Record<string, unknown>
  const gate = s.safety_gate_passed
  const failed = rows.filter((r) => !r.passed)
  const passedCount = rows.length - failed.length
  const shown = shownAll
  const modelName = models.data?.items.find((m) => String(m.id) === run.data?.model_id)?.model_name ?? `模型 ${run.data.model_id}`
  const total = run.data.case_count || rows.length
  const done = Math.max(run.data.done, rows.length)

  return (
    <div className="cb-col cb-eval-run">
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
      <Stats>
        {done < total ? <StatCard value={`${formatInt(done)} / ${formatInt(total)}`} label="已完成 / 总题数" /> : <StatCard value={formatInt(total)} label="总题数" />}
        <StatCard value={formatInt(passedCount)} label="通过" tone="success" />
        <StatCard value={formatInt(failed.length)} label="未通过" tone={failed.length ? 'danger' : undefined} />
        <StatCard value={done ? pct(passedCount / done) : '—'} label={running ? '当前通过率' : '通过率'} />
        {!running && num(s.total) !== undefined ? (
          <>
            <StatCard value={pct(s.first_pass_rate)} label="一次通过率" />
            <StatCard value={pct(s.blocked_pass_rate)} label="安全题拦截率" />
            <StatCard value={formatMs(Math.round(num(s.average_duration_ms) ?? 0))} label="平均耗时" />
            <StatCard value={`${formatInt(num(s.tokens_in) ?? 0)} / ${formatInt(num(s.tokens_out) ?? 0)}`} label="Token 输入 / 输出" />
          </>
        ) : null}
      </Stats>
      {rows.length ? (
        <>
          <div className="cb-filters">
            <Tabs
              label="结果筛选"
              value={filter}
              onChange={(v) => {
                setFilter(v)
                paged.setPage(1)
              }}
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
              onChange={(v) => {
                setCategory(v)
                paged.setPage(1)
              }}
              options={[{ value: '', label: '全部分类' }, ...categories.map((c) => ({ value: c, label: `${CATEGORY[c] ?? c}（${rows.filter((r) => r.category === c && r.passed).length}/${rows.filter((r) => r.category === c).length}）` }))]}
            />
          </div>
          <ListCard label="逐题结果" className="cb-eval-results" pager={<Pager {...paged.pager} />}>
            <ListStatus
              loading={false}
              count={shown.length}
              filtered
              onClear={() => {
                setFilter('all')
                setCategory('')
              }}
              empty={null}
            >
              <table className="cb-data cb-data-fixed">
                <thead>
                  <tr>
                    <th style={{ width: 72 }}>题号</th>
                    <th>问题</th>
                    <th style={{ width: '12%' }}>分类</th>
                    <th style={{ width: 104 }}>结果</th>
                    <th style={{ width: '24%' }}>原因</th>
                    <th style={{ width: 72 }}>耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {paged.rows.map((r) => (
                    <tr key={r.case_id} className="cb-eval-row" onClick={() => setOpen(r.case_id)}>
                      <td className="cb-m">{r.case_id}</td>
                      <td title={r.case?.question}>{r.case?.question ?? <span className="cb-faint">—</span>}</td>
                      <td>{CATEGORY[r.category] ?? r.category}</td>
                      <td>{r.passed ? <Badge tone="success">通过</Badge> : <Badge tone="danger">未通过</Badge>}</td>
                      <td className={r.passed ? 'cb-faint' : undefined} title={r.passed ? undefined : reasonText(r)}>{r.passed ? '—' : reasonText(r)}</td>
                      <td className="cb-r">{formatMs(r.duration_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ListStatus>
          </ListCard>
          <div className="cb-muted">点击一行查看期望与实际的对比，以及提问账号、纠错次数和 Token。</div>
          {open != null && shown.some((r) => r.case_id === open) ? (
            <ResultDialog results={shown} caseId={open} admin={admin} onPick={setOpen} onClose={() => setOpen(null)} />
          ) : null}
        </>
      ) : running ? <div className="cb-muted">第一题正在运行…</div> : null}
    </div>
  )
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
            <Stats>
              <StatCard value={formatInt(r.expected_row_count ?? 0)} label="期望行数" />
              <StatCard value={formatInt(r.actual_row_count ?? 0)} label="实际行数" />
              <StatCard value={r.matched_rows == null ? '—' : formatInt(r.matched_rows)} label="一致行数" tone={summary?.tone === 'success' ? 'success' : summary?.tone === 'danger' ? 'danger' : undefined} />
              <StatCard value={MODE[mode] ?? mode} label="比对方式" />
            </Stats>
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
