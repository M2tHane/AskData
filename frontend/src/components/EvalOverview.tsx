import { useRef, useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { getEvalRun } from '../api/hooks'
import type { RunListItem } from '../api/types'
import { Segmented } from './basic'
import { ChartPlot, useWidth, type ChartModel } from './charts'
import { EmptyState } from './ListStates'
import { StatCard, Stats } from './StatCard'
import { formatSpan } from '../lib/format'
import { formatPt, overviewStats, pct, trendRuns } from '../lib/evalText'

type Range = '10' | '30d'

const rateOf = (summary: Record<string, unknown> | undefined) => (typeof summary?.blocked_pass_rate === 'number' ? summary.blocked_pass_rate : null)

/** 概览：四张统计卡 + 通过率、安全题拦截率的趋势折线。 */
export function EvalOverview({ runs, bankSize = 0 }: { runs: RunListItem[]; bankSize?: number }) {
  const [range, setRange] = useState<Range>('10')
  const ref = useRef<HTMLDivElement>(null)
  const width = useWidth(ref)
  const stats = overviewStats(runs, undefined, bankSize)
  const points = trendRuns(runs, range === '10' ? { last: 10 } : { days: 30 }, undefined, bankSize)
  // 安全题拦截率只在单次评测的汇总里；和评测详情共用同一份缓存。
  const details = useQueries({ queries: [...points, ...(stats.latest && !points.some((p) => p.id === stats.latest?.id) ? [stats.latest] : [])].map((r) => ({ queryKey: ['eval', 'run', r.id], queryFn: () => getEvalRun(r.id), staleTime: 60_000 })) })
  const summaryOf = (id: number) => details.find((d) => d.data?.id === id)?.data?.summary as Record<string, unknown> | undefined
  const safety = stats.latest ? rateOf(summaryOf(stats.latest.id)) : null
  const withSafety = points.length > 0 && points.every((p) => rateOf(summaryOf(p.id)) != null)
  const model: ChartModel = {
    series: withSafety ? ['通过率', '安全题拦截率'] : ['通过率'],
    formats: { 通过率: 'percent', 安全题拦截率: 'percent' },
    temporal: true,
    data: points.map((p) => ({ label: `#${p.id}`, x: null, values: withSafety ? [p.passed / p.done, rateOf(summaryOf(p.id)) as number] : [p.passed / p.done] })),
  }

  return (
    <div className="cb-col">
      <Stats label="评测概况">
        <StatCard
          label="最近通过率"
          title="最近一次跑完整个题库的评测里通过的比例；只挑几道题的复测不计入"
          value={
            <>
              {stats.passRate == null ? '—' : pct(stats.passRate)}
              {stats.deltaPt != null ? <span className={stats.deltaPt < 0 ? 'cb-eval-delta cb-eval-delta-down' : stats.deltaPt > 0 ? 'cb-eval-delta cb-eval-delta-up' : 'cb-eval-delta'}>比上次 {formatPt(stats.deltaPt)}</span> : null}
            </>
          }
        />
        <StatCard label="安全题拦截率" title="最近一次跑完整个题库的评测里，越权、注入等安全题被拦截的比例" value={safety == null ? '—' : pct(safety)} />
        <StatCard label="近 30 天评测" value={stats.count30} unit=" 次" />
        <StatCard label="平均用时" title="跑完整个题库的评测平均用时" value={stats.avgMs == null ? '—' : formatSpan(stats.avgMs)} />
      </Stats>
      <section className="cb-panel cb-eval-trend" aria-label="通过率趋势">
        <div className="cb-panel-head">
          <div className="cb-eval-trend-title">
            <h3 className="cb-h">通过率趋势</h3>
            <span className="cb-muted">只统计跑完整个题库的评测</span>
          </div>
          <Segmented<Range> label="趋势范围" value={range} onChange={setRange} items={[{ id: '10', label: '近 10 次' }, { id: '30d', label: '近 30 天' }]} />
        </div>
        <div className="cb-eval-trend-body" ref={ref}>
          {points.length >= 2 ? (
            width ? <ChartPlot model={model} kind="line" width={width} title="通过率趋势" /> : <div className="cb-chart-wait" />
          ) : (
            <EmptyState icon="chart" title={runs.length ? '跑完两次整个题库后显示趋势' : '还没有评测'}>
              {runs.length ? '趋势只统计跑完整个题库的评测；停止、中断或只挑几道题的评测不计入。' : '到「评测记录」新建一次评测。'}
            </EmptyState>
          )}
        </div>
      </section>
    </div>
  )
}
