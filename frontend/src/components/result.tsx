import { useRef, useState } from 'react'
import type { ChartSpec } from '../api/types'
import { columnFormat, formatDateLabels, formatValue, isNumericColumn } from '../lib/format'
import { Badge, Button } from './basic'
import { ChartKindPicker, ChartPlot, availableKinds, chartModel, initialKind, usePngExport, useWidth, type ChartKind } from './charts'
import { Icon } from './Icon'

export const TABLE_ROWS = 30

export interface ResultCardProps {
  spec?: ChartSpec | null
  columns: string[]
  rows: Record<string, unknown>[]
  /** 服务端实际返回的总行数。 */
  total: number
  truncated: boolean
  /** 表格最多显示的行数，默认 30。 */
  tableRows?: number
  /** 导出上限说明，例如「最多 10 万行、50 MB」。 */
  exportLimitHint?: string
  onExport?: (format: 'csv' | 'xlsx') => void
}

/** 一轮回答的结果：左上切换图表/表格，右上是图表类型或导出；导出会重新执行 SQL 取全量。 */
export function ResultCard(props: ResultCardProps) {
  const { spec, columns, rows, total, truncated, exportLimitHint, onExport } = props
  const limit = props.tableRows ?? TABLE_ROWS
  const plotRef = useRef<HTMLDivElement>(null)
  const width = useWidth(plotRef)
  const model = spec ? chartModel(spec, rows) : null
  const [mode, setMode] = useState<'chart' | 'table' | null>(null)
  const [picked, setKind] = useState<ChartKind | null>(null)
  const title = spec?.title || '查询结果'
  const exportPng = usePngExport(plotRef, title)
  const view = model ? (mode ?? 'chart') : 'table'
  const kinds = model ? availableKinds(model) : []
  const kind = model && spec ? (picked && kinds.includes(picked) ? picked : initialKind(spec, model, width)) : 'bar'
  const shown = rows.slice(0, limit)
  const labels = spec?.labels ?? {}
  const numeric = new Set(columns.filter((c) => isNumericColumn(rows, c)))
  const formats = spec?.formats
  // 只有一行、全是数值（例如「订单金额合计」）时，用大数字展示，比一行表格更好读。
  const kpi = rows.length === 1 && columns.length > 0 && columns.length <= 4 && columns.every((c) => numeric.has(c))
  const dates = new Map(columns.filter((c) => !numeric.has(c)).map((c) => [c, formatDateLabels(shown.map((r) => (typeof r[c] === 'string' ? (r[c] as string) : '')))]))

  return (
    <section className="cb-result-card" aria-label="查询结果">
      <div className="cb-result-head">
        {model && !kpi ? (
          <div className="cb-seg cb-seg-text" role="group" aria-label="展示方式">
            <button type="button" aria-pressed={view === 'chart'} onClick={() => setMode('chart')}>
              <Icon name="chart" size="sm" />
              图表
            </button>
            <button type="button" aria-pressed={view === 'table'} onClick={() => setMode('table')}>
              <Icon name="table" size="sm" />
              表格
            </button>
          </div>
        ) : null}
        <span className="cb-h">{title}</span>
        <span className="cb-spacer" />
        {view === 'chart' && model && !kpi ? (
          <>
            <ChartKindPicker kinds={kinds} value={kind} onChange={setKind} />
            <Button size="sm" variant="ghost" icon="download" aria-label="导出 PNG" onClick={() => void exportPng()}>
              PNG
            </Button>
          </>
        ) : onExport ? (
          <>
            <Button size="sm" variant="ghost" icon="download" onClick={() => onExport('csv')}>
              导出 CSV
            </Button>
            <Button size="sm" variant="ghost" icon="download" onClick={() => onExport('xlsx')}>
              导出 Excel
            </Button>
          </>
        ) : null}
      </div>
      <div className={view === 'chart' && !kpi ? 'cb-result-body cb-chart-plot' : 'cb-result-body'} ref={plotRef}>
        {kpi ? (
          <div className="cb-kpis">
            {columns.map((c) => (
              <div key={c} className="cb-kpi">
                <div className="cb-kpi-label">{labels[c] ?? c}</div>
                <div className="cb-kpi-value cb-num">{cell(rows[0]?.[c], columnFormat(c, rows, formats))}</div>
              </div>
            ))}
          </div>
        ) : view === 'chart' && model ? (
          <ChartPlot model={model} kind={kind} width={width} title={title} />
        ) : (
          <div className="cb-table-wrap">
            <table className="cb-table">
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th key={c} className={numeric.has(c) ? 'cb-r' : undefined} title={labels[c] ? c : undefined}>
                      {labels[c] ?? c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((row, i) => (
                  <tr key={i}>
                    {columns.map((c) => (
                      <td key={c} className={numeric.has(c) ? 'cb-r' : undefined}>
                        {typeof row[c] === 'string' ? (dates.get(c)?.[i] ?? String(row[c])) : cell(row[c], numeric.has(c) ? columnFormat(c, rows, formats) : undefined)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {kpi && !truncated ? null : (
        <div className="cb-result-foot">
          <span className="cb-num">
            共 {total.toLocaleString('zh-CN')} 行
            {view === 'table' && total > shown.length ? `，表格显示前 ${shown.length.toLocaleString('zh-CN')} 行` : ''}
          </span>
          {truncated ? (
            <Badge tone="warning" icon="alert">
              已达行数上限
            </Badge>
          ) : null}
          {/* 导出按钮只在表格视图出现，说明也只在那时显示。 */}
          {onExport && view === 'table' && (total > shown.length || truncated) ? <span>· 导出会重新执行查询获取全部数据{exportLimitHint ? `（${exportLimitHint}）` : ''}</span> : null}
        </div>
      )}
    </section>
  )
}

function cell(value: unknown, format?: ReturnType<typeof columnFormat>) {
  if (value == null) return <span className="cb-muted">—</span>
  if (format) return formatValue(value, format)
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
