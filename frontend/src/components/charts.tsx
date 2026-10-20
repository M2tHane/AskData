import { useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'
import type { ChartSpec } from '../api/types'
import { chartToPng, saveBlob } from '../lib/png'
import { axisFormatter, formatAxis, formatDateLabels, formatValue, inferFormat, seriesFormatter, toNumber, type NumberFormat } from '../lib/format'
import { Button } from './basic'
import { Icon, type IconName } from './Icon'
import { useToast } from './Toast'

export interface ChartModel {
  data: { label: string; x: number | null; values: number[] }[]
  /** 系列名：结果列名；长表透视时是分组值。 */
  series: string[]
  /** 长表透视时的指标列，系列共用它的显示名和格式。 */
  measure?: string
  labels?: Record<string, string>
  formats?: Record<string, NumberFormat>
  y_right?: string[]
  /** 横轴是时间（或图表配置要折线）：只有这时才提供折线图。 */
  temporal?: boolean
  /** 各系列同一单位（如各团队的 Token）：量级再悬殊也共用一根纵轴。 */
  singleAxis?: boolean
}

const DATE_LIKE = /^(\d{4}([-/.年]\d{1,2}([-/.月]\d{1,2}日?)?月?|\s*(Q|第)[1-4]季?度?)|\d{4}年?)$|^\d{1,2}月$|^\d{4}-W\d{2}$/

/** 横轴标签像日期、月份、季度或年份。 */
export function isTemporal(labels: string[]): boolean {
  return labels.length > 0 && labels.every((l) => DATE_LIKE.test(l.trim()))
}

export type ChartKind = 'bar' | 'hbar' | 'stacked' | 'line' | 'pie' | 'scatter'

const isNumber = (v: unknown) => toNumber(v) !== null

/** 图表数据只来自结果里真实存在的列。x 为空且只有一行时，把每个数值列当作一根柱（宽表转长表）。 */
export function chartModel(spec: ChartSpec, rows: Record<string, unknown>[]): ChartModel | null {
  if (spec.type === 'table') return null
  const ys = spec.y ?? []
  const extra = { labels: spec.labels ?? {}, formats: spec.formats ?? {}, y_right: spec.y_right ?? [] }
  if (spec.series && spec.x && ys.length === 1) {
    const model = pivotSeries(spec.x, spec.series, ys[0] as string, rows, extra)
    return model && { ...model, temporal: spec.type === 'line' || isTemporal(model.data.map((d) => d.label)) }
  }
  if (ys.length === 0 || ys.length > 5 || rows.length === 0) return null
  if (!spec.x) {
    const only = rows[0]
    if (spec.type === 'scatter' || rows.length !== 1 || !only) return null
    const values = ys.map((k) => toNumber(only[k]))
    if (!values.every((v) => v !== null)) return null
    return { data: ys.map((k, i) => ({ label: spec.labels?.[k] ?? k, x: null, values: [values[i] as number] })), series: ['值'], ...extra, y_right: [] }
  }
  const x = spec.x
  if (!rows.every((r) => ys.every((k) => r[k] == null || isNumber(r[k])))) return null
  const labels = formatDateLabels(rows.map((r) => String(r[x] ?? '')))
  const data = rows.map((r, i) => ({ label: labels[i] ?? '', x: toNumber(r[x]), values: ys.map((k) => toNumber(r[k]) ?? 0) }))
  if (spec.type === 'scatter' && !data.every((d) => d.x !== null)) return null
  return { data, series: ys, ...extra, temporal: spec.type === 'line' || isTemporal(labels) }
}

/** 长表（x、分组列、一个指标）转成每个分组一条系列；超过 5 个分组时保留合计最大的 4 个，其余并成「其他」。 */
function pivotSeries(x: string, split: string, y: string, rows: Record<string, unknown>[], extra: Pick<ChartModel, 'labels' | 'formats'>): ChartModel | null {
  const labels: string[] = []
  const names: string[] = []
  const cells = new Map<string, number>()
  const shown = formatDateLabels(rows.map((r) => String(r[x] ?? '')))
  const totals = new Map<string, number>()
  for (const r of rows) {
    const name = String(r[split] ?? '')
    totals.set(name, (totals.get(name) ?? 0) + Math.abs(toNumber(r[y]) ?? 0))
  }
  const keep = new Set([...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, totals.size > 5 ? 4 : 5).map(([name]) => name))
  for (const [i, r] of rows.entries()) {
    const label = shown[i] ?? ''
    const raw = String(r[split] ?? '')
    const name = keep.has(raw) ? raw : '其他'
    const value = toNumber(r[y])
    if (value === null) return null
    if (!labels.includes(label)) labels.push(label)
    if (!names.includes(name)) names.push(name)
    const key = `${label}\u0000${name}`
    cells.set(key, (cells.get(key) ?? 0) + value)
  }
  if (names.length < 2 || names.length > 5) return null
  const data = labels.map((label) => ({ label, x: toNumber(label), values: names.map((name) => cells.get(`${label}\u0000${name}`) ?? 0) }))
  return { data, series: names, measure: y, ...extra, y_right: [] }
}

/** 系列的显示格式：指标列的格式；没有则按列名和取值推断。 */
export function seriesFormat(model: ChartModel, j: number): NumberFormat {
  const column = model.measure ?? model.series[j] ?? ''
  return model.formats?.[column] ?? inferFormat(column, model.data.map((d) => d.values[j]))
}

export function seriesLabel(model: ChartModel, j: number): string {
  const name = model.series[j] ?? ''
  return model.measure ? name : (model.labels?.[name] ?? name)
}

/** 放右轴的系列下标：图表配置指定的优先；否则量级相差 20 倍以上或比率与金额并列时自动拆分。 */
export function rightAxis(model: ChartModel): number[] {
  if (model.measure || model.singleAxis || model.series.length < 2) return []
  const explicit = (model.y_right ?? []).map((name) => model.series.indexOf(name)).filter((j) => j >= 0)
  if (explicit.length && explicit.length < model.series.length) return explicit
  const peaks = model.series.map((_, j) => Math.max(...model.data.map((d) => Math.abs(d.values[j] ?? 0))))
  const top = Math.max(...peaks)
  const percent = model.series.map((_, j) => seriesFormat(model, j) === 'percent')
  const right = model.series
    .map((_, j) => j)
    .filter((j) => (percent[j] && percent.some((p) => !p)) || (!percent[j] && (peaks[j] ?? 0) * 20 < top))
  return right.length < model.series.length ? right : []
}

/** 占比图只在单一非负指标、2–5 个类别时成立。 */
export function pieEligible(model: ChartModel): boolean {
  return (
    model.series.length === 1 &&
    model.data.length >= 2 &&
    model.data.length <= 5 &&
    model.data.every((d) => (d.values[0] ?? -1) >= 0) &&
    model.data.some((d) => (d.values[0] ?? 0) > 0)
  )
}

/** 当前数据能画哪些图；顺序即工具条顺序。 */
export function availableKinds(model: ChartModel): ChartKind[] {
  const dual = rightAxis(model).length > 0
  const kinds: ChartKind[] = ['bar']
  if (!dual) kinds.push('hbar')
  if (!dual && model.series.length > 1 && model.data.every((d) => d.values.every((v) => v >= 0))) kinds.push('stacked')
  // 折线表示连续变化，只给时间或数值横轴；类别排名连成线没有意义。
  if (model.temporal || (model.data.length >= 2 && model.data.every((d) => d.x !== null))) kinds.push('line')
  if (pieEligible(model)) kinds.push('pie')
  if (model.data.length >= 2 && model.data.every((d) => d.x !== null)) kinds.push('scatter')
  return kinds
}

/** 12px 字号（字重 500）下的文字宽度估算：中文 12px，其他 7px。size 换算其他字号。 */
export function textWidth(text: string, size = 12): number {
  let w = 0
  for (const ch of text) w += /[⺀-鿿＀-￯]/.test(ch) ? 12 : 7
  return (w * size) / 12
}

function clip(text: string, width: number): string {
  if (textWidth(text) <= width) return text
  let out = ''
  for (const ch of text) {
    if (textWidth(out + ch + '…') > width) break
    out += ch
  }
  return out ? `${out}…` : ''
}

/** 类别名过长或类别太多时，柱状图改为横向条形图，避免横轴文字重叠。 */
export function prefersHorizontal(model: ChartModel, width: number): boolean {
  if (rightAxis(model).length) return false
  const slot = (width - 60) / Math.max(model.data.length, 1)
  return model.data.length > 12 || model.data.some((d) => textWidth(d.label) > slot - 6)
}

export function initialKind(spec: ChartSpec, model: ChartModel, width = 640): ChartKind {
  const kinds = availableKinds(model)
  const want: ChartKind = spec.type === 'bar' && spec.stack ? 'stacked' : spec.type === 'table' ? 'bar' : spec.type
  const kind = kinds.includes(want) ? want : 'bar'
  return kind === 'bar' && model.series.length === 1 && prefersHorizontal(model, width) ? 'hbar' : kind
}

export const chartData = (spec: ChartSpec, rows: Record<string, unknown>[]) => chartModel(spec, rows)?.data ?? null

export const KIND_LABEL: Record<ChartKind, { label: string; icon: IconName }> = {
  bar: { label: '柱状图', icon: 'chart' },
  hbar: { label: '条形图', icon: 'hbar' },
  stacked: { label: '堆叠柱状图', icon: 'stack' },
  line: { label: '折线图', icon: 'line' },
  pie: { label: '占比图', icon: 'pie' },
  scatter: { label: '散点图', icon: 'scatter' },
}

/**
 * 含 0 的整齐刻度：步长取 1/2/2.5/5×10ⁿ，约 4 格，上限只比最大值多出不到一格。
 * 全是整数（订单数、人数）时步长也取整数，不出现 0.5、2.5 这样的刻度。
 */
export function niceScale(values: number[], target = 4): { lo: number; hi: number; ticks: number[] } {
  const max = Math.max(0, ...values)
  const min = Math.min(0, ...values)
  const span = max - min || Math.abs(max) || 1
  const raw = span / target
  const pow = Math.pow(10, Math.floor(Math.log10(raw)))
  const integers = values.length > 0 && values.every(Number.isInteger)
  const steps = integers && pow < 10 ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10]
  const nice = (steps.find((m) => m * pow >= raw) ?? 10) * pow
  const step = integers ? Math.max(1, Math.round(nice)) : nice
  const lo = Math.floor(min / step) * step
  const hi = Math.max(Math.ceil(max / step) * step, lo + step)
  const ticks: number[] = []
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toPrecision(12)))
  return { lo, hi, ticks }
}

const SCATTER_TICKS = [0, 0.25, 0.5, 0.75, 1]

/** 只画图形本身（不含标题和工具条），供 ChartCard 与结果卡片共用。 */
export function ChartPlot(props: { model: ChartModel; kind: ChartKind; width: number; title: string }) {
  const { model, kind, width, title } = props
  const { data, series } = model
  const right = kind === 'bar' || kind === 'line' ? rightAxis(model) : []
  const left = series.map((_, j) => j).filter((j) => !right.includes(j))
  const fmtOf = (j: number) => seriesFormat(model, j)
  const legend = series.length > 1 && kind !== 'pie' && kind !== 'scatter'
  const marks: ReactNode[] = []
  const B = 26
  let T = 10
  let H = 240
  if (legend) {
    // 图例画在 SVG 里，导出 PNG 时一并带上；放不下时换行。
    let cx = 4
    let row = 0
    series.forEach((_, j) => {
      const text = `${seriesLabel(model, j)}${right.includes(j) ? '（右轴）' : ''}`
      const w = 16 + textWidth(text, 13) + 16
      if (cx > 4 && cx + w > width) {
        cx = 4
        row += 1
      }
      marks.push(
        <rect key={`lg${j}`} className={`cb-s${j + 1}`} x={cx} y={6 + row * 20} width={10} height={10} rx={2} style={{ stroke: 'none' }} />,
        <text key={`lt${j}`} className="cb-legend-t" x={cx + 15} y={15 + row * 20}>
          {text}
        </text>,
      )
      cx += w
    })
    T += (row + 1) * 20 + 4
    H += (row + 1) * 20
  }

  if (kind === 'pie') {
    const total = data.reduce((a, d) => a + (d.values[0] ?? 0), 0)
    const r = H / 2 - 14
    const cx = Math.max(r + 8, Math.min(width / 2 - 100, width - r - 220))
    const cy = H / 2
    let angle = -Math.PI / 2
    data.forEach((d, i) => {
      const share = (d.values[0] ?? 0) / total
      const next = angle + share * 2 * Math.PI
      const at = (a: number) => `${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`
      const path = share >= 0.9999 ? `M${cx},${cy - r} A${r},${r} 0 1 1 ${cx - 0.01},${cy - r} Z` : `M${cx},${cy} L${at(angle)} A${r},${r} 0 ${share > 0.5 ? 1 : 0} 1 ${at(next)} Z`
      const text = `${d.label}：${formatValue(d.values[0] ?? 0, fmtOf(0))}（${(share * 100).toFixed(1)}%）`
      marks.push(
        <path key={`p${i}`} className={`cb-s${i + 1}`} d={path} style={{ stroke: 'var(--bg-card)', strokeWidth: 1.5 }}>
          <title>{text}</title>
        </path>,
        <rect key={`pk${i}`} className={`cb-s${i + 1}`} x={cx + r + 28} y={cy - 56 + i * 24} width={10} height={10} rx={2} style={{ stroke: 'none' }} />,
        <text key={`pt${i}`} className="cb-legend-t" x={cx + r + 44} y={cy - 47 + i * 24}>
          <title>{text}</title>
          {clip(`${d.label} ${(share * 100).toFixed(1)}%`, ((width - (cx + r + 48)) * 12) / 13)}
        </text>,
      )
      angle = next
    })
  } else if (kind === 'hbar') {
    const rowH = Math.max(22, Math.min(32, 12 + 10 * left.length))
    H = T + data.length * rowH + B
    const longest = Math.max(...data.map((d) => textWidth(d.label)))
    const L = Math.min(Math.max(48, longest + 12), width * 0.4)
    const values = data.flatMap((d) => left.map((j) => d.values[j] ?? 0))
    const { lo, hi, ticks } = niceScale(values)
    const tickText = axisFormatter(ticks, fmtOf(left[0] ?? 0))
    const valueText = left.length === 1 ? seriesFormatter(values, fmtOf(left[0] ?? 0)) : undefined
    const valueGutter = valueText ? Math.max(48, ...data.map((d) => textWidth(valueText(d.values[left[0] ?? 0] ?? 0)) + 12)) : 12
    const px = (v: number) => L + (width - L - valueGutter) * ((v - lo) / (hi - lo || 1))
    ticks.forEach((v, i) => {
      marks.push(<line key={`g${i}`} className="cb-grid" x1={px(v)} x2={px(v)} y1={T} y2={H - B} />)
      marks.push(
        <text key={`gt${i}`} className="cb-axis" x={px(v)} y={H - 8} textAnchor="middle">
          {tickText(v)}
        </text>,
      )
    })
    const barH = Math.min(18, (rowH - 8) / left.length)
    data.forEach((d, i) => {
      const top = T + rowH * i + (rowH - barH * left.length) / 2
      marks.push(
        <text key={`y${i}`} className="cb-axis" x={L - 8} y={T + rowH * i + rowH / 2 + 4} textAnchor="end">
          <title>{d.label}</title>
          {clip(d.label, L - 12)}
        </text>,
      )
      left.forEach((j, k) => {
        const v = d.values[j] ?? 0
        const x0 = px(Math.min(0, v))
        marks.push(
          <rect key={`h${i}-${j}`} className={`cb-s${j + 1}`} x={x0} y={top + k * barH} width={Math.max(1, Math.abs(px(v) - px(0)))} height={barH - 2} rx={2} style={{ stroke: 'none' }}>
            <title>{`${d.label} ${seriesLabel(model, j)}：${formatValue(v, fmtOf(j))}`}</title>
          </rect>,
        )
        if (valueText)
          marks.push(
            <text key={`hv${i}`} className="cb-val" x={px(Math.max(0, v)) + 6} y={top + barH / 2 + 3}>
              {valueText(v)}
            </text>,
          )
      })
    })
  } else {
    const R = right.length ? 52 : 12
    const L = 52
    const leftValues = kind === 'stacked' ? data.map((d) => left.reduce((a, j) => a + (d.values[j] ?? 0), 0)) : data.flatMap((d) => left.map((j) => d.values[j] ?? 0))
    const lDomain = niceScale(leftValues)
    const rDomain = niceScale(data.flatMap((d) => right.map((j) => d.values[j] ?? 0)))
    const leftTick = axisFormatter(lDomain.ticks, fmtOf(left[0] ?? 0))
    const rightTick = axisFormatter(rDomain.ticks, fmtOf(right[0] ?? 0))
    const scale = (dom: { lo: number; hi: number }) => (v: number) => T + (H - T - B) * (1 - (v - dom.lo) / (dom.hi - dom.lo || 1))
    const yl = scale(lDomain)
    const yr = scale(rDomain)
    const xs = data.map((d) => d.x ?? 0)
    const xlo = Math.min(...xs)
    const xhi = Math.max(...xs)
    const plotW = width - L - R
    lDomain.ticks.forEach((v, i) => {
      marks.push(<line key={`g${i}`} className="cb-grid" x1={L} x2={width - R} y1={yl(v)} y2={yl(v)} />)
      marks.push(
        <text key={`gt${i}`} className="cb-axis" x={L - 8} y={yl(v) + 4} textAnchor="end">
          {leftTick(v)}
        </text>,
      )
    })
    if (right.length)
      rDomain.ticks.forEach((rv, i) =>
        marks.push(
          <text key={`rt${i}`} className="cb-axis" x={width - R + 8} y={yr(rv) + 4}>
            {rightTick(rv)}
          </text>,
        ),
      )
    if (kind === 'scatter') {
      const px = (v: number) => L + 8 + (plotW - 16) * ((v - xlo) / (xhi - xlo || 1))
      SCATTER_TICKS.forEach((f, i) =>
        marks.push(
          <text key={`xt${i}`} className="cb-axis" x={px(xlo + (xhi - xlo) * f)} y={H - 8} textAnchor="middle">
            {formatAxis(xlo + (xhi - xlo) * f)}
          </text>,
        ),
      )
      series.forEach((_, j) =>
        data.forEach((d, i) =>
          marks.push(
            <circle key={`s${j}-${i}`} className={`cb-s${j + 1}`} cx={px(d.x ?? 0)} cy={yl(d.values[j] ?? 0)} r={4.5} style={{ fillOpacity: 0.75, stroke: 'var(--bg-card)', strokeWidth: 1 }}>
              <title>{`${seriesLabel(model, j)}：(${d.label}, ${formatValue(d.values[j] ?? 0, fmtOf(j))})`}</title>
            </circle>,
          ),
        ),
      )
    } else {
      const bw = plotW / Math.max(data.length, 1)
      // 标签放不下时隔几个显示一个，并截断到槽宽，保证互不重叠。
      const widest = Math.max(...data.map((d) => Math.min(textWidth(d.label), 96)))
      const every = Math.max(1, Math.ceil((widest + 8) / bw))
      data.forEach((d, i) => {
        if (i % every) return
        marks.push(
          <text key={`x${i}`} className="cb-axis" x={L + bw * i + bw / 2} y={H - 8} textAnchor="middle">
            <title>{d.label}</title>
            {clip(d.label, bw * every - 8)}
          </text>,
        )
      })
      if (kind === 'bar') {
        const inner = Math.min(bw * 0.7, 28 * left.length + 4 * (left.length - 1))
        const each = (inner - 4 * (left.length - 1)) / Math.max(left.length, 1)
        // 单系列柱子顶上标数值（放得下时），很小的值也能读出来。
        const only = left.length === 1 && right.length === 0 ? left[0] : undefined
        const valueText = only != null ? seriesFormatter(data.map((d) => d.values[only] ?? 0), fmtOf(only)) : undefined
        const labelled = valueText != null && data.every((d) => textWidth(valueText(d.values[only ?? 0] ?? 0)) <= bw - 4)
        data.forEach((d, i) =>
          left.forEach((j, k) => {
            const v = d.values[j] ?? 0
            const x = L + bw * i + (bw - inner) / 2 + k * (each + 4)
            marks.push(
              <rect key={`b${i}-${j}`} className={`cb-s${j + 1}`} x={x} y={Math.min(yl(v), yl(0))} width={each} height={Math.max(1, Math.abs(yl(0) - yl(v)))} rx={2} style={{ stroke: 'none' }}>
                <title>{`${d.label} ${seriesLabel(model, j)}：${formatValue(v, fmtOf(j))}`}</title>
              </rect>,
            )
            if (labelled && valueText)
              marks.push(
                <text key={`bv${i}`} className="cb-val" x={x + each / 2} y={v >= 0 ? yl(v) - 5 : yl(v) + 12} textAnchor="middle">
                  {valueText(v)}
                </text>,
              )
          }),
        )
      } else if (kind === 'stacked') {
        const inner = Math.min(bw * 0.6, 48)
        data.forEach((d, i) => {
          let acc = 0
          left.forEach((j) => {
            const v = d.values[j] ?? 0
            const from = acc
            acc += v
            marks.push(
              <rect key={`k${i}-${j}`} className={`cb-s${j + 1}`} x={L + bw * i + (bw - inner) / 2} y={yl(acc)} width={inner} height={Math.max(0, yl(from) - yl(acc))} style={{ stroke: 'var(--bg-card)', strokeWidth: 1 }}>
                <title>{`${d.label} ${seriesLabel(model, j)}：${formatValue(v, fmtOf(j))}`}</title>
              </rect>,
            )
          })
        })
      }
      // 折线：line 图的全部系列；柱状图里放右轴的系列也画成折线叠在柱子上。
      const lines = kind === 'line' ? series.map((_, j) => j) : right
      lines.forEach((j) => {
        const y = right.includes(j) ? yr : yl
        const pts = data.map((d, i) => [L + bw * i + bw / 2, y(d.values[j] ?? 0)] as const)
        marks.push(<polyline key={`l${j}`} className={`cb-s${j + 1}`} points={pts.map((q) => q.join(',')).join(' ')} style={{ fill: 'none', strokeWidth: 2, strokeLinejoin: 'round' }} />)
        pts.forEach((q, i) =>
          marks.push(
            <circle key={`c${j}-${i}`} className={`cb-s${j + 1}`} cx={q[0]} cy={q[1]} r={3} style={{ stroke: 'var(--bg-card)', strokeWidth: 1.5 }}>
              <title>{`${data[i]?.label ?? ''} ${seriesLabel(model, j)}：${formatValue(data[i]?.values[j] ?? 0, fmtOf(j))}`}</title>
            </circle>,
          ),
        )
      })
    }
  }

  return (
    <svg viewBox={`0 0 ${width} ${H}`} width={width} height={H} role="img" aria-label={title}>
      {marks}
    </svg>
  )
}

/** 容器内容区的宽度（去掉内边距）。 */
function contentWidth(el: HTMLElement): number {
  const style = getComputedStyle(el)
  return Math.round(el.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0))
}

/**
 * 图形按容器实际宽度绘制：ResizeObserver 在首帧绘制前报告宽度，之后跟随变化。
 * 还没量到（隐藏或布局未完成，宽度为 0）时返回 null，调用方先不画，
 * 避免按固定宽度画好再被 viewBox 放大、文字变得特别大。
 * 没有 ResizeObserver 的环境（测试）按 640 画。
 */
export function useWidth(ref: RefObject<HTMLElement | null>): number | null {
  const [width, setWidth] = useState<number | null>(() => (typeof ResizeObserver === 'undefined' ? 640 : null))
  // 图形区可能晚于首次渲染才出现（数据还没到时组件先返回空）：每次渲染后检查一次，换了元素就重新观察。
  const [el, setEl] = useState<HTMLElement | null>(null)
  // Runs after every render on purpose; the guard makes it settle after one update.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    if (ref.current !== el) setEl(ref.current)
  })
  useLayoutEffect(() => {
    if (!el || typeof ResizeObserver === 'undefined') return
    // ResizeObserver reports the first size before the next paint, so nothing is drawn at a guessed width.
    const ro = new ResizeObserver(() => {
      const w = contentWidth(el)
      if (w > 0) setWidth(w)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  return width
}

export function ChartKindPicker(props: { kinds: ChartKind[]; value: ChartKind; onChange: (kind: ChartKind) => void }) {
  return (
    <div className="cb-chart-kinds" role="group" aria-label="图表类型">
      {props.kinds.map((k) => (
        <button key={k} type="button" aria-pressed={props.value === k} aria-label={KIND_LABEL[k].label} title={KIND_LABEL[k].label} onClick={() => props.onChange(k)}>
          <Icon name={KIND_LABEL[k].icon} size="sm" />
        </button>
      ))}
    </div>
  )
}

/** 把图形区的 svg 导出为 PNG。 */
export function usePngExport(ref: RefObject<HTMLElement | null>, title: string): () => Promise<void> {
  const toast = useToast()
  return async () => {
    const svg = ref.current?.querySelector('svg')
    if (!svg) return
    try {
      saveBlob(await chartToPng(svg, title), `${title.replace(/[\\/:*?"<>|\s]+/g, '_')}.png`)
    } catch (e) {
      toast({ tone: 'danger', title: '导出图片失败', message: e instanceof Error ? e.message : undefined })
    }
  }
}

export function ChartCard(props: { spec: ChartSpec; rows: Record<string, unknown>[]; singleAxis?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const width = useWidth(ref)
  const [picked, setKind] = useState<ChartKind | null>(null)
  const title = props.spec.title || '图表'
  const exportPng = usePngExport(ref, title)
  const built = chartModel(props.spec, props.rows)
  if (!built) return null
  const model = props.singleAxis ? { ...built, singleAxis: true } : built
  const kinds = availableKinds(model)
  const kind = picked && kinds.includes(picked) ? picked : initialKind(props.spec, model, width ?? undefined)
  return (
    <figure className="cb-chart" style={{ margin: 0 }}>
      <div className="cb-chart-head">
        <span className="cb-h">{title}</span>
        <span className="cb-spacer" />
        <ChartKindPicker kinds={kinds} value={kind} onChange={setKind} />
        <Button size="sm" variant="ghost" icon="download" aria-label="导出 PNG" onClick={() => void exportPng()}>
          PNG
        </Button>
      </div>
      <div className="cb-chart-plot" ref={ref}>
        {width ? <ChartPlot model={model} kind={kind} width={width} title={title} /> : <div className="cb-chart-wait" />}
      </div>
    </figure>
  )
}
