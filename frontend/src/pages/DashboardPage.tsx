import { useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useReplay } from '../api/hooks'
import { Button } from '../components/basic'
import { ChartPlot, availableKinds, chartModel, initialKind, useWidth } from '../components/charts'
import { Icon } from '../components/Icon'
import { EmptyState, LoadError, SkeletonRows } from '../components/ListStates'
import { HeaderActions } from '../components/PageHeader'
import { useToast } from '../components/Toast'
import { columnFormat, formatTime, formatValue, isNumericColumn } from '../lib/format'
import { useDashboard, type Pin } from '../state/dashboard'

/** 一张看板卡片：原问题 + 数据源与刷新时间 + 图表或单值大数字；按原 SQL 重新查询。 */
function PinCard({ pin }: { pin: Pin }) {
  const navigate = useNavigate()
  const toast = useToast()
  const unpin = useDashboard((s) => s.unpin)
  const restore = useDashboard((s) => s.restore)
  const reply = useReplay(pin.conversationId, pin.messageId, true)
  const plot = useRef<HTMLDivElement>(null)
  const width = useWidth(plot)
  const data = reply.data
  const rows = data?.rows ?? []
  const columns = data?.columns ?? []
  const spec = data?.chart ?? pin.chart ?? null
  const model = spec ? chartModel(spec, rows) : null
  const kpi = rows.length === 1 && columns.length > 0 && columns.every((c) => isNumericColumn(rows, c))
  const kinds = model ? availableKinds(model) : []
  const kind = model && spec ? initialKind(spec, model, width ?? undefined) : 'bar'
  const failed = reply.isError || (data && data.code !== 'OK')

  return (
    <section className="cb-panel cb-dcard" aria-label={pin.question}>
      <div className="cb-dcard-head">
        <div>
          <b title={pin.question}>{pin.question}</b>
          <span className="cb-sub">
            {pin.datasource} · {reply.dataUpdatedAt ? `刷新于 ${formatTime(new Date(reply.dataUpdatedAt).toISOString())}` : '刷新中'}
          </span>
        </div>
        <button type="button" className="cb-icon-btn" aria-label="刷新" title="刷新" disabled={reply.isFetching} onClick={() => void reply.refetch()}>
          <Icon name="refresh" />
        </button>
        <button type="button" className="cb-icon-btn" aria-label="在对话中打开" title="在对话中打开" onClick={() => navigate(`/chat/${pin.conversationId}`)}>
          <Icon name="chat" />
        </button>
        <button
          type="button"
          className="cb-icon-btn"
          aria-label="取消固定"
          title="取消固定"
          onClick={() => {
            const gone = unpin(pin.id)
            toast({ tone: 'info', title: '已取消固定', message: pin.question, action: gone ? { label: '撤销', onClick: () => restore(gone) } : undefined })
          }}
        >
          <Icon name="x" />
        </button>
      </div>
      <div className="cb-dcard-body" ref={plot}>
        {reply.isPending ? (
          <SkeletonRows rows={4} cols={3} />
        ) : failed ? (
          <LoadError error={reply.error ?? undefined} what="这张卡片" onRetry={() => void reply.refetch()} />
        ) : kpi ? (
          <div className="cb-kpis">
            {columns.map((c) => (
              <div key={c} className="cb-kpi">
                <small>{spec?.labels?.[c] ?? c}</small>
                <b className="cb-dcard-kpi">{formatValue(rows[0]?.[c], columnFormat(c, rows, spec?.formats))}</b>
              </div>
            ))}
          </div>
        ) : model && kinds.length && width ? (
          <div className="cb-chart-plot">
            <ChartPlot model={model} kind={kind} width={width} title={pin.question} />
          </div>
        ) : (
          <p className="cb-muted">共 {rows.length.toLocaleString('zh-CN')} 行，这个结果不适合画图，在对话中打开查看表格。</p>
        )}
      </div>
    </section>
  )
}

/**
 * 我的看板（VITE_FEATURE_DASHBOARD）：在对话里点「固定到看板」加进来。
 * 后端还没有看板接口，卡片记在本机，刷新时按原 SQL 重新查询。
 */
export function DashboardPage() {
  const qc = useQueryClient()
  const pins = useDashboard((s) => s.pins)
  return (
    <div className="cb-page">
      <HeaderActions>
        <Button icon="refresh" disabled={!pins.length} onClick={() => void qc.invalidateQueries({ queryKey: ['replay'] })}>
          全部刷新
        </Button>
      </HeaderActions>
      {pins.length ? (
        <div className="cb-dash">
          {pins.map((p) => (
            <PinCard key={p.id} pin={p} />
          ))}
        </div>
      ) : (
        <div className="cb-panel">
          <EmptyState icon="grid" title="看板还是空的">
            在对话里点回答下方的「固定到看板」，常看的结果会出现在这里，并按原 SQL 重新查询。
          </EmptyState>
        </div>
      )}
    </div>
  )
}
