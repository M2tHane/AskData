import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ResultCard } from './components/result'
import { chartModel, initialKind, rightAxis } from './components/charts'
import { formatSql } from './components/chat'
import type { ChartSpec } from './api/types'

const products = Array.from({ length: 50 }, (_, i) => ({
  product_name: `山野折叠屏手机（家庭装）型号 ${i + 1}`,
  sales_amount: 12148.25 - i * 100,
  gross_margin: 0.3927848553 - i * 0.001,
  phone: '13812345678',
}))
const spec: ChartSpec = {
  type: 'bar',
  x: 'product_name',
  y: ['sales_amount', 'gross_margin'],
  stack: false,
  title: '销售额最高的商品',
  labels: { product_name: '商品', sales_amount: '销售额', gross_margin: '毛利率' },
  formats: { sales_amount: 'currency', gross_margin: 'percent' },
}
const base = { columns: ['product_name', 'sales_amount', 'gross_margin', 'phone'], rows: products, total: 50, truncated: false }

describe('ResultCard', () => {
  it('opens on the chart, with the chart kinds and PNG on the right', () => {
    render(<ResultCard {...base} spec={spec} />)
    expect(screen.getByRole('img', { name: '销售额最高的商品' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByRole('group', { name: '图表类型' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '导出 PNG' })).toBeInTheDocument()
    // The giant-arrow bug: the PNG icon must not be a plot svg.
    expect(screen.getByRole('button', { name: '导出 PNG' }).querySelector('svg')?.closest('.cb-chart-plot')).toBeNull()
  })

  it('switches to a 30-row table with display names, formatted numbers and export', async () => {
    const onExport = vi.fn()
    render(<ResultCard {...base} spec={spec} exportLimitHint="最多 10 万行、50 MB" onExport={onExport} />)
    await userEvent.click(screen.getByRole('button', { name: /表格/ }))
    const table = screen.getByRole('table')
    expect(table.querySelectorAll('tbody tr')).toHaveLength(30)
    expect(within(table).getByText('销售额')).toBeInTheDocument()
    expect(within(table).getByText('12,148.25')).toBeInTheDocument()
    expect(within(table).getByText('39.3%')).toBeInTheDocument()
    // Digit strings such as phone numbers are text, not numbers.
    expect(within(table).getAllByText('13812345678').length).toBeGreaterThan(0)
    expect(screen.getByText(/共 50 行，表格显示前 30 行/)).toBeInTheDocument()
    expect(screen.getByText(/导出会重新执行查询获取全部数据（最多 10 万行、50 MB）/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '导出 Excel' }))
    expect(onExport).toHaveBeenCalledWith('xlsx')
  })

  it('shows only the table when nothing is drawable', () => {
    render(<ResultCard {...base} spec={{ type: 'table', stack: false, title: '' }} truncated />)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: '展示方式' })).toBeNull()
    expect(screen.getByText('已达行数上限')).toBeInTheDocument()
  })
})

describe('single values', () => {
  it('shows a one-row numeric result as big numbers instead of a table', () => {
    render(<ResultCard columns={['sales_amount']} rows={[{ sales_amount: 92219.25 }]} total={1} truncated={false} spec={{ type: 'table', stack: false, title: '订单销售额合计', labels: { sales_amount: '销售额（元）' }, formats: { sales_amount: 'currency' } }} />)
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByText('销售额（元）')).toBeInTheDocument()
    expect(screen.getByText('92,219.25')).toBeInTheDocument()
    expect(screen.queryByText(/共 1 行/)).toBeNull()
  })
})

describe('chart layout', () => {
  it('picks a tight axis and labels every bar in one unit', async () => {
    const { niceScale } = await import('./components/charts')
    expect(niceScale([12148.25, 8399]).ticks).toEqual([0, 5000, 10000, 15000])
    expect(niceScale([6345.3, 164.05]).hi).toBe(8000)
    const rows = [{ r: '华东', v: 6345.3 }, { r: '华中', v: 164.05 }]
    render(<ResultCard columns={['r', 'v']} rows={rows} total={2} truncated={false} spec={{ type: 'bar', x: 'r', y: ['v'], stack: false, title: '各地区', formats: { v: 'currency' } }} />)
    expect(screen.getByText('6,345')).toBeInTheDocument()
    expect(screen.getByText('164')).toBeInTheDocument()
  })

  it('puts a ratio next to an amount on a right axis', () => {
    const model = chartModel(spec, products.slice(0, 10))!
    expect(rightAxis(model)).toEqual([1])
    render(<ResultCard {...base} rows={products.slice(0, 10)} total={10} spec={spec} />)
    expect(screen.getByText('毛利率（右轴）')).toBeInTheDocument()
  })

  it('draws long category names as horizontal bars', () => {
    const one = { ...spec, y: ['sales_amount'] }
    const model = chartModel(one, products.slice(0, 10))!
    expect(initialKind(one, model, 700)).toBe('hbar')
    const short = chartModel(one, [{ product_name: 'A', sales_amount: 1 }, { product_name: 'B', sales_amount: 2 }])!
    expect(initialKind(one, short, 700)).toBe('bar')
  })

  it('reads decimals delivered as strings', () => {
    const model = chartModel({ type: 'bar', x: 'k', y: ['v'], stack: false, title: '' }, [{ k: 'a', v: '12148.2500000000000000' }])
    expect(model?.data[0]?.values).toEqual([12148.25])
  })
})

describe('formatSql', () => {
  it('breaks top-level clauses and keeps literals and quoted names intact', () => {
    const sql = `WITH "alloc" AS (SELECT "i"."id" AS "id" FROM "items" AS "i" LEFT JOIN "o" ON "o"."id" = "i"."oid" WHERE "o"."note" = 'from where group by') SELECT "id" FROM "alloc" ORDER BY "id" LIMIT 10`
    const out = formatSql(sql)
    expect(out).toContain("'from where group by'")
    expect(out.split('\n')).toEqual([
      'WITH "alloc" AS (',
      '  SELECT "i"."id" AS "id"',
      '  FROM "items" AS "i"',
      '  LEFT JOIN "o" ON "o"."id" = "i"."oid"',
      `  WHERE "o"."note" = 'from where group by')`,
      'SELECT "id"',
      'FROM "alloc"',
      'ORDER BY "id"',
      'LIMIT 10',
    ])
  })
})

describe('ReasoningPanel', () => {
  it('is open while thinking, folds when the answer starts, and respects a manual toggle', async () => {
    const { ReasoningPanel } = await import('./components/reasoning')
    const { rerender } = render(<ReasoningPanel text="先找订单表" thinking answering={false} />)
    expect(screen.getByText('先找订单表')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /正在思考/ })).toHaveAttribute('aria-expanded', 'true')
    rerender(<ReasoningPanel text="先找订单表，再排序" thinking={false} answering seconds={12} />)
    expect(screen.queryByText('先找订单表，再排序')).toBeNull()
    const head = screen.getByRole('button', { name: /已思考 12 秒/ })
    expect(head).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(head)
    expect(screen.getByText('先找订单表，再排序')).toBeInTheDocument()
    rerender(<ReasoningPanel text="先找订单表，再排序" thinking={false} answering seconds={12} />)
    expect(screen.getByText('先找订单表，再排序')).toBeInTheDocument()
  })
  it('renders nothing for models that do not stream thinking', async () => {
    const { ReasoningPanel } = await import('./components/reasoning')
    const { container } = render(<ReasoningPanel text="" thinking={false} answering />)
    expect(container).toBeEmptyDOMElement()
  })
})
