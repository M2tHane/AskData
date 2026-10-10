import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useEvalCompare, useEvalResults } from '../api/hooks'
import type { Evaluation, RunListItem } from '../api/types'
import { Badge, Button, Callout } from './basic'
import { EmptyState, ListCard, ListStatus } from './ListStates'
import { Pager, usePaged } from './Pager'
import { Select } from './Select'
import { StatCard, Stats } from './StatCard'
import { useToast } from './Toast'
import { ApiError, friendlyError } from '../lib/errors'
import { FEATURES } from '../lib/features'
import { formatSpan } from '../lib/format'
import { changeRatio, formatPt, passRate, reasonText, runLabel } from '../lib/evalText'

/**
 * 只重跑评测里的一道题，返回这道题的新结果。
 * 后端还没有这个接口，界面由 FEATURES.evalRerunCase 控制，默认不显示。
 * 建议接口：POST /api/eval/runs/{run_id}/cases/{case_id}/rerun，无请求体，
 * 返回更新后的 Evaluation（同 GET /api/eval/runs/{run_id}/results 里的一项），并把这次结果写回该评测。
 */
export const rerunEvalCase = (runId: number, caseId: string) => api<Evaluation>('POST', `/api/eval/runs/${runId}/cases/${caseId}/rerun`)

const rate = (r: RunListItem | undefined) => (r && r.done ? r.passed / r.done : null)

/** 两次评测并排：通过率和耗时的变化，新增失败的题带原因和去处，已修复、新增、移除的题列在下面。 */
export function EvalCompare({ runs, admin }: { runs: RunListItem[]; admin: boolean }) {
  const navigate = useNavigate()
  const toast = useToast()
  const qc = useQueryClient()
  const [before, setBefore] = useState<number | null>(runs[1]?.id ?? null)
  const [after, setAfter] = useState<number | null>(runs[0]?.id ?? null)
  const [rerunning, setRerunning] = useState<string | null>(null)
  const cmp = useEvalCompare(before, after)
  const results = useEvalResults(after)
  const byCase = new Map((results.data ?? []).map((r) => [r.case_id, r]))
  const failures = cmp.data?.new_failures ?? []
  const paged = usePaged(failures)
  const a = runs.find((r) => r.id === before)
  const b = runs.find((r) => r.id === after)
  const delta = rate(a) != null && rate(b) != null ? Math.round(((rate(b) as number) - (rate(a) as number)) * 1000) / 10 : null
  const dur = a && b && a.duration_ms > 0 && b.duration_ms > 0 ? changeRatio(a.duration_ms, b.duration_ms) : null

  const rerun = async (caseId: string) => {
    if (after == null) return
    setRerunning(caseId)
    try {
      const r = await rerunEvalCase(after, caseId)
      await qc.invalidateQueries({ queryKey: ['eval'] })
      toast(r.passed ? { tone: 'success', title: `${caseId} 重跑通过` } : { tone: 'warning', title: `${caseId} 重跑仍未通过`, message: reasonText(r) })
    } catch (e) {
      toast({ tone: 'danger', title: `${caseId} 没能重跑`, message: e instanceof ApiError ? friendlyError(e.code).title : '请稍后重试' })
    } finally {
      setRerunning(null)
    }
  }

  if (runs.length < 2) {
    return (
      <EmptyState icon="flask" title="至少需要两次评测才能对比">
        {admin ? '到「评测记录」新建一次评测，再回来对比。' : '请管理员启动评测。'}
      </EmptyState>
    )
  }
  const options = runs.map((r) => ({ value: r.id, label: runLabel(r) }))
  const chips = (title: string, items: string[] | undefined, tone: 'success' | 'neutral') =>
    items?.length ? (
      <div className="cb-eval-chips">
        <span className="cb-eval-chips-title">{title}（{items.length}）</span>
        {items.map((c) => <Badge key={c} tone={tone} mono>{c}</Badge>)}
      </div>
    ) : null

  return (
    <div className="cb-col">
      <div className="cb-filters">
        <label className="cb-eval-pick">
          <span>基线</span>
          <Select ariaLabel="基线评测" value={before} onChange={(v) => { setBefore(v); paged.setPage(1) }} options={options} />
        </label>
        <label className="cb-eval-pick">
          <span>对比</span>
          <Select ariaLabel="对比评测" value={after} onChange={(v) => { setAfter(v); paged.setPage(1) }} options={options} />
        </label>
      </div>
      {before === after ? <Callout tone="warning">请选择两次不同的评测。</Callout> : null}
      {cmp.error instanceof ApiError ? <Callout tone="danger">{cmp.error.message}</Callout> : null}
      <Stats label="两次评测的变化">
        <StatCard label="基线通过率" value={a ? passRate(a) : '—'} />
        <StatCard label="对比通过率" value={b ? passRate(b) : '—'} />
        <StatCard label="通过率变化" value={delta == null ? '—' : formatPt(delta)} tone={delta == null || delta === 0 ? undefined : delta > 0 ? 'success' : 'danger'} />
        <StatCard
          label="耗时变化"
          value={dur == null ? '—' : `${dur > 0 ? '+' : ''}${dur}%`}
          help={a && b ? `${a.duration_ms ? formatSpan(a.duration_ms) : '—'} 到 ${b.duration_ms ? formatSpan(b.duration_ms) : '—'}` : undefined}
        />
      </Stats>
      {before === after ? null : (
      <ListCard title={`新增失败的题（${failures.length}）`} label="新增失败的题" className="cb-eval-fails" pager={<Pager {...paged.pager} />}>
        <ListStatus
          loading={cmp.isFetching && !cmp.data}
          error={cmp.error instanceof ApiError ? undefined : cmp.error}
          onRetry={() => void cmp.refetch()}
          what="对比结果"
          count={failures.length}
          cols={4}
          empty={<EmptyState icon="check" title="没有新增失败的题">对比的评测比基线没有多错任何一道题。</EmptyState>}
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                <th style={{ width: 72 }}>题号</th>
                <th>问题</th>
                <th style={{ width: '28%' }}>失败原因</th>
                <th style={{ width: admin ? (FEATURES.evalRerunCase ? 288 : 192) : 0 }}>{admin ? <span className="cb-sr">操作</span> : null}</th>
              </tr>
            </thead>
            <tbody>
              {paged.rows.map((id) => {
                const r = byCase.get(id)
                const reason = r ? reasonText(r) : '—'
                return (
                  <tr key={id}>
                    <td className="cb-m">{id}</td>
                    <td title={r?.case?.question}>{r?.case?.question ?? <span className="cb-faint">—</span>}</td>
                    <td title={reason}>{reason}</td>
                    <td>
                      {admin ? (
                        <div className="cb-actions">
                          <Button size="sm" variant="ghost" onClick={() => navigate('/semantic')}>去改语义</Button>
                          <Button size="sm" variant="ghost" onClick={() => navigate('/prompts')}>去改提示词</Button>
                          {FEATURES.evalRerunCase ? (
                            <Button size="sm" variant="ghost" icon="play" disabled={rerunning != null} onClick={() => void rerun(id)}>重跑这道题</Button>
                          ) : null}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>
      )}
      {chips('已修复', cmp.data?.fixed, 'success')}
      {chips('新增题', cmp.data?.added, 'neutral')}
      {chips('移除题', cmp.data?.removed, 'neutral')}
    </div>
  )
}
