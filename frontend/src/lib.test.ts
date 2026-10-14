import { buildFields } from './lib/fields'
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { parseSse } from './api/client'
import { availableKinds, initialKind, niceScale } from './components/charts'
import { activeMention, chartData, chartModel, pieEligible, filterMetrics, mentionedIds } from './components/chat'
import { navFor } from './lib/nav'
import { formatSpan, formatTime } from './lib/format'
import { describeError } from './lib/errors'
import { applyEvent, newTurn, provenance, revokedText, starters, turnFromReply } from './pages/turns'

describe('sse parser', () => {
  it('splits frames and keeps the partial tail', () => {
    const raw = 'id: 1\nevent: stage\ndata: {"name":"retrieval"}\n\nid: 2\nevent: sql\ndata: {"sql":"SELECT 1"}\n\nid: 3\nevent: do'
    const { events, rest } = parseSse(raw)
    expect(events).toEqual([
      { event: 'stage', data: { name: 'retrieval' } },
      { event: 'sql', data: { sql: 'SELECT 1' } },
    ])
    expect(rest).toBe('id: 3\nevent: do')
  })
  it('drops malformed frames without throwing', () => {
    expect(parseSse('event: sql\ndata: {oops\n\n').events).toEqual([])
  })
})

describe('turn reducer', () => {
  it('accumulates a successful stream', () => {
    let t = newTurn('q', 0)
    t = applyEvent(t, { event: 'stage', data: { name: 'generate_sql' } })
    t = applyEvent(t, { event: 'sql', data: { sql: 'SELECT 1' } })
    t = applyEvent(t, { event: 'data', data: { columns: ['a'], rows: [{ a: 1 }], truncated: false } })
    t = applyEvent(t, { event: 'explanation', data: { meta: { interpretation: '按金额', filters: ['已支付'], reasoning: '', question: 'q' } } })
    t = applyEvent(t, { event: 'answer', data: { delta: '共 1 ' } })
    t = applyEvent(t, { event: 'answer', data: { delta: '行' } })
    t = applyEvent(t, { event: 'title', data: { title: '金额' } })
    t = applyEvent(t, { event: 'done', data: { status: 'success', query_run_id: 7 } }, 1500)
    expect(t).toMatchObject({ status: 'success', sql: 'SELECT 1', queryRunId: 7, ms: 1500, answer: '共 1 行', title: '金额' })
    expect(t.meta?.filters).toEqual(['已支付'])
  })
  it('ends thinking at the first non-thinking output', () => {
    let t = newTurn('q', 0)
    t = applyEvent(t, { event: 'reasoning', data: { delta: '先看' } }, 100)
    t = applyEvent(t, { event: 'reasoning', data: { delta: '订单表' } }, 900)
    expect(t).toMatchObject({ reasoning: '先看订单表', reasoningStartedAt: 100 })
    expect(t.reasoningEndedAt).toBeUndefined()
    t = applyEvent(t, { event: 'sql', data: { sql: 'SELECT 1' } }, 2100)
    expect(t.reasoningEndedAt).toBe(2100)
    t = applyEvent(t, { event: 'answer', data: { delta: 'x' } }, 3000)
    expect(t.reasoningEndedAt).toBe(2100)
  })
  it('takes the clarification message id from the done event', () => {
    let t = newTurn('q', 0)
    t = applyEvent(t, { event: 'explanation', data: { clarification: { question: '哪种？', choices: ['a', 'b'] } } })
    t = applyEvent(t, { event: 'done', data: { status: 'clarify', message_id: 42 } })
    expect(t.clarifyMessageId).toBe(42)
  })
  it('records clarification and error codes', () => {
    let t = newTurn('q', 0)
    t = applyEvent(t, { event: 'explanation', data: { clarification: { question: '哪种？', choices: ['a', 'b'] } } })
    t = applyEvent(t, { event: 'done', data: { status: 'clarify' } })
    expect(t.status).toBe('clarify')
    expect(t.clarification?.choices).toEqual(['a', 'b'])
    const failed = applyEvent(newTurn('q'), { event: 'error', data: { code: 'SQL_OUT_OF_SCOPE', message: 'x' } })
    expect(failed.errorCode).toBe('SQL_OUT_OF_SCOPE')
  })
  it('builds a turn from a rerun reply', () => {
    const t = turnFromReply('改 SQL', { status: 'blocked', code: 'SQL_NOT_SELECT', explanation: '', truncated: false, row_count: 0, preview_truncated: false, reread: false, masks_changed: false })
    expect(t).toMatchObject({ status: 'blocked', errorCode: 'SQL_NOT_SELECT', rows: [] })
  })
  it('says where an earlier result came from and what was revoked', () => {
    const now = new Date(2026, 9, 13, 20, 0)
    expect(provenance({}, now)).toBeUndefined()
    expect(provenance({ queriedAt: new Date(2026, 9, 13, 19, 5).toISOString() }, now)).toBe('查询于 今天 19:05')
    expect(provenance({ queriedAt: new Date(2026, 9, 12, 9, 30).toISOString(), reread: true }, now)).toBe('重新查询于 昨天 09:30（原结果未保存）')
    expect(provenance({ queriedAt: new Date(2026, 8, 1, 8, 0).toISOString() }, now)).toBe('查询于 09-01 08:00')
    expect(provenance({ queriedAt: new Date(2025, 8, 1, 8, 0).toISOString() }, now)).toBe('查询于 2025-09-01 08:00')
    expect(revokedText(['orders.pay_amount', 'customers.phone'])).toBe('orders.pay_amount、customers.phone 你已无权查看，思考过程、结论和图表不再显示。')
    expect(revokedText([])).toBe('这条回答用到的部分表或字段你已无权查看，思考过程、结论和图表不再显示。')
  })
})

describe('chart data', () => {
  const rows = [{ k: 'a', v: 1 }, { k: 'b', v: 2 }]
  it('maps bar/line specs and rejects the rest', () => {
    expect(chartData({ type: 'bar', x: 'k', y: ['v'], stack: false, title: '' }, rows)).toEqual([{ label: 'a', x: null, values: [1] }, { label: 'b', x: null, values: [2] }])
    expect(chartData({ type: 'pie', x: 'k', y: ['v'], stack: false, title: '' }, rows)).toEqual([{ label: 'a', x: null, values: [1] }, { label: 'b', x: null, values: [2] }])
    expect(chartData({ type: 'scatter', x: 'k', y: ['v'], stack: false, title: '' }, rows)).toBeNull()
    expect(chartData({ type: 'bar', x: 'k', y: ['k'], stack: false, title: '' }, rows)).toBeNull()
  })
})

describe('navigation by role', () => {
  it('hides entries the role cannot use instead of disabling them', () => {
    const ids = (r: 'admin' | 'leader' | 'member') => navFor(r).routes.map((x) => x.id)
    expect(ids('member')).toEqual(['chat', 'semantic', 'metrics'])
    expect(ids('leader')).toContain('logs')
    expect(ids('leader')).toContain('permissions')
    expect(ids('leader')).not.toContain('org')
    expect(ids('leader')).not.toContain('usage')
    expect(ids('leader')).not.toContain('datasources')
    expect(ids('admin')).toContain('datasources')
  })
  it('keeps business entries grouped on top and governance entries flat at the bottom', () => {
    const admin = navFor('admin')
    expect(admin.groups.map((g) => g.label)).toEqual(['查数', '数据'])
    expect(admin.bottom.map((r) => r.id)).toEqual(['feedback', 'logs', 'eval', 'org', 'mcp', 'models', 'prompts', 'usage'])
    expect(navFor('member').bottom).toEqual([])
  })
})

describe('formatting', () => {
  it('formats relative days', () => {
    const now = new Date(2026, 9, 7, 12, 0)
    expect(formatTime(new Date(2026, 9, 7, 14, 22).toISOString(), now)).toBe('今天 14:22')
    expect(formatTime(new Date(2026, 9, 6, 9, 0).toISOString(), now)).toBe('昨天')
    expect(formatTime(new Date(2026, 8, 30, 9, 0).toISOString(), now)).toBe('2026-09-30')
  })
  it('error text names category and code, never objects', () => {
    expect(describeError('SQL_MULTI_STATEMENT')).toContain('原因：SQL_MULTI_STATEMENT')
  })
})

describe('number formatting', () => {
  it('converts numbers and numeric strings to number', async () => {
    const { toNumber } = await import('./lib/format')
    expect(toNumber(123)).toBe(123)
    expect(toNumber('456')).toBe(456)
    expect(toNumber('7.89')).toBe(7.89)
    expect(toNumber('1.2e5')).toBe(120000)
    expect(toNumber('abc')).toBeNull()
    expect(toNumber(null)).toBeNull()
    expect(toNumber(undefined)).toBeNull()
  })
  it('formats values with appropriate decimal places', async () => {
    const { formatValue } = await import('./lib/format')
    expect(formatValue(123)).toBe('123')
    expect(formatValue(1234.567, 'number')).toBe('1,234.57')
    expect(formatValue(1234, 'integer')).toBe('1,234')
    expect(formatValue(0.25, 'percent')).toBe('25%')
    expect(formatValue(0.126, 'percent')).toBe('12.6%')
    expect(formatValue(12.5, 'currency')).toBe('12.50')
    expect(formatValue(null)).toBe('—')
  })
  it('formats axis values with compact notation', async () => {
    const { axisFormatter, seriesFormatter } = await import('./lib/format')
    // 同一组数用同一单位、同样的小数位。
    const v = seriesFormatter([12148.25, 8399, 164.05], 'currency')
    expect([v(12148.25), v(8399), v(164.05)]).toEqual(['12,148', '8,399', '164'])
    const w = seriesFormatter([1_234_567, 80_000])
    expect([w(1_234_567), w(80_000)]).toEqual(['123.5万', '8.0万'])
    const ticks = axisFormatter([0, 5000, 10000, 15000])
    expect([0, 5000, 15000].map(ticks)).toEqual(['0', '5,000', '15,000'])
    expect(axisFormatter([0, 100000, 200000])(150000)).toBe('15万')
    const { formatAxis } = await import('./lib/format')
    expect(formatAxis(0.5, 'percent')).toBe('50%')
    expect(formatAxis(1234567)).toBe('123.5万')
    expect(formatAxis(123456789)).toBe('1.2亿')
    expect(formatAxis(456.78)).toBe('456.78')
  })
  it('infers format from column name and values', async () => {
    const { inferFormat } = await import('./lib/format')
    expect(inferFormat('profit_margin', [0.15, 0.2, 0.1])).toBe('percent')
    expect(inferFormat('count', [1, 2, 3])).toBe('integer')
    expect(inferFormat('sales_amount', [1000.5, 2000.3])).toBe('number')
  })
})

describe('design tokens only', () => {
  const walk = (dir: string): string[] =>
    readdirSync(dir).flatMap((n) => {
      const p = join(dir, n)
      return statSync(p).isDirectory() ? walk(p) : [p]
    })
  const files = walk('src').filter((f) => /\.(tsx|ts|css)$/.test(f) && !/(tokens|components)\.css$|schema\.d\.ts$|\.test\./.test(f))
  it('has no hard-coded hex colors outside the token files', () => {
    const offenders = files.filter((f) => /#[0-9a-fA-F]{3,8}\b/.test(readFileSync(f, 'utf8').replace(/&#\d+;/g, '')))
    expect(offenders).toEqual([])
  })
  it('has no rgb()/hsl() literals outside the token files', () => {
    expect(files.filter((f) => /\b(rgba?|hsla?)\(/.test(readFileSync(f, 'utf8')))).toEqual([])
  })
})

describe('answer helpers', () => {
  it('drops the technical text of legacy answers', async () => {
    const { legacyAnswer, conclusion, formatTimeRanges } = await import('./pages/turns')
    const raw = ['查询完成。', '生效语义/指标：semantic 销售额：{"aliases": []}', '过滤条件与时间范围以已校验 SQL 为准：SELECT 1'].join('\n')
    expect(legacyAnswer(raw)).toBe('')
    expect(legacyAnswer('第一行\n第二行')).toBe('第一行\n第二行')
    expect(formatTimeRanges({ 上个月: { start: '2026-09-01', end_exclusive: '2026-10-01' } })).toBe('上个月：2026-09-01 至 2026-09-30')
    expect(formatTimeRanges({})).toBeUndefined()
    expect(conclusion(0, false)).toBe('没有查到符合条件的数据。')
    expect(conclusion(1284, true)).toBe('共返回 1,284 行，结果已截断。')
  })
})

describe('where clause extraction', () => {
  it('returns the filter without grouping, ordering or limits', async () => {
    const { extractWhereClause } = await import('./lib/sql')
    expect(extractWhereClause('SELECT a FROM t WHERE region = \'华东区\' AND "d" >= \'2026-09-01\' GROUP BY a ORDER BY a LIMIT 10')).toBe(
      'region = \'华东区\' AND "d" >= \'2026-09-01\'',
    )
    expect(extractWhereClause('SELECT 1 FROM t')).toBeNull()
    expect(extractWhereClause(null)).toBeNull()
  })
  it('maps codes to a title and a next step', async () => {
    const { friendlyError } = await import('./lib/errors')
    expect(friendlyError('SQL_OUT_OF_SCOPE')).toMatchObject({ code: 'SQL_OUT_OF_SCOPE', hint: expect.stringContaining('团队负责人') })
    expect(friendlyError('NOPE')).toMatchObject({ title: '请求失败', code: 'NOPE', showCode: true })
    expect(friendlyError('QUERY_CANCELLED')).toMatchObject({ title: '已停止生成', showCode: false })
    const { codeFromMessage } = await import('./lib/errors')
    expect(codeFromMessage('Internal error')).toBe('INTERNAL_ERROR')
    expect(codeFromMessage('上月销售额 1,000 元')).toBeUndefined()
  })
})

describe('chart model', () => {
  it('turns a one-row wide aggregate into one bar per column', () => {
    const model = chartModel({ type: 'bar', x: null, y: ['a_cnt', 'b_cnt'], stack: false, title: '' }, [{ a_cnt: 4, b_cnt: 9 }])
    expect(model?.data).toEqual([{ label: 'a_cnt', x: null, values: [4] }, { label: 'b_cnt', x: null, values: [9] }])
    expect(model?.series).toEqual(['值'])
    expect(chartModel({ type: 'bar', x: null, y: ['a_cnt'], stack: false, title: '' }, [{ a_cnt: 4 }, { a_cnt: 5 }])).toBeNull()
  })
  it('handles numeric strings as numbers', () => {
    const model = chartModel({ type: 'bar', x: 'k', y: ['v'], stack: false, title: '' }, [
      { k: 'a', v: '123.45' },
      { k: 'b', v: '67.89' },
    ])
    expect(model?.data[0]?.values).toEqual([123.45])
    expect(model?.data[1]?.values).toEqual([67.89])
  })
})

describe('metric mentions', () => {
  const metrics = [{ id: 1, name: 'GMV', aliases: ['成交额'] }, { id: 2, name: '回款金额', aliases: [] }]
  it('detects the @ token only at a word boundary', () => {
    expect(activeMention('上月 @GM', 7)).toEqual({ start: 3, query: 'GM' })
    expect(activeMention('a@b', 3)).toBeNull()
    expect(activeMention('@GMV 是多少', 8)).toBeNull()
  })
  it('filters by name or alias and keeps only mentions still in the text', () => {
    expect(filterMetrics(metrics, '成交').map((m) => m.id)).toEqual([1])
    expect(filterMetrics(metrics, '').length).toBe(2)
    expect(mentionedIds('看一下 @GMV 的趋势', metrics)).toEqual([1])
    expect(mentionedIds('看一下趋势', metrics)).toEqual([])
  })
})

describe('pieEligible', () => {
  const m = (values: number[]) => ({ series: ['v'], data: values.map((v, i) => ({ label: `c${i}`, x: null, values: [v] })) })
  it('requires 2-5 non-negative categories with a positive total', () => {
    expect(pieEligible(m([1, 2, 3]))).toBe(true)
    expect(pieEligible(m([1]))).toBe(false)
    expect(pieEligible(m([1, 2, 3, 4, 5, 6]))).toBe(false)
    expect(pieEligible(m([1, -2]))).toBe(false)
    expect(pieEligible(m([0, 0]))).toBe(false)
    expect(pieEligible({ series: ['a', 'b'], data: [{ label: 'x', x: null, values: [1, 2] }, { label: 'y', x: null, values: [1, 2] }] })).toBe(false)
  })
})

describe('chart kinds', () => {
  const rows = [{ k: 'a', n: 1, m: 3 }, { k: 'b', n: 2, m: 4 }]
  it('offers stacked only for several non-negative series', () => {
    const multi = chartModel({ type: 'bar', x: 'k', y: ['n', 'm'], stack: false, title: '' }, rows)
    expect(multi && availableKinds(multi)).toEqual(['bar', 'hbar', 'stacked'])
    const one = chartModel({ type: 'bar', x: 'k', y: ['n'], stack: false, title: '' }, rows)
    expect(one && availableKinds(one)).toEqual(['bar', 'hbar', 'pie'])
    const negative = chartModel({ type: 'bar', x: 'k', y: ['n', 'm'], stack: false, title: '' }, [{ k: 'a', n: -1, m: 3 }, { k: 'b', n: 2, m: 4 }])
    expect(negative && availableKinds(negative)).not.toContain('stacked')
  })
  it('offers a line only for a time axis, and shortens month starts', () => {
    const months = chartModel({ type: 'bar', x: 'm', y: ['n'], stack: false, title: '' }, [
      { m: '2026-01-01T00:00:00', n: 1 },
      { m: '2026-02-01T00:00:00', n: 2 },
    ])
    expect(months?.data.map((d) => d.label)).toEqual(['2026-01', '2026-02'])
    expect(months && availableKinds(months)).toContain('line')
    const ranking = chartModel({ type: 'line', x: 'k', y: ['n'], stack: false, title: '' }, rows)
    expect(ranking && availableKinds(ranking)).toContain('line')
    expect(chartModel({ type: 'bar', x: 'k', y: ['n'], stack: false, title: '' }, rows)?.temporal).toBe(false)
  })
  it('folds more than five groups into the top four and 其他', () => {
    const data = ['a', 'b', 'c', 'd', 'e', 'f'].map((g, i) => ({ m: '2026-01', g, n: 10 - i }))
    const model = chartModel({ type: 'line', x: 'm', series: 'g', y: ['n'], stack: false, title: '' }, data)
    expect(model?.series).toEqual(['a', 'b', 'c', 'd', '其他'])
    expect(model?.data[0]?.values).toEqual([10, 9, 8, 7, 11])
  })
  it('keeps the spec kind when the data supports it and falls back to bar otherwise', () => {
    const multi = chartModel({ type: 'bar', x: 'k', y: ['n', 'm'], stack: true, title: '' }, rows)
    expect(multi && initialKind({ type: 'bar', x: 'k', y: ['n', 'm'], stack: true, title: '' }, multi)).toBe('stacked')
    const one = chartModel({ type: 'bar', x: 'k', y: ['n'], stack: false, title: '' }, rows)
    expect(one && initialKind({ type: 'pie', x: 'k', y: ['n'], stack: false, title: '' }, one)).toBe('pie')
    expect(one && initialKind({ type: 'scatter', x: 'k', y: ['n'], stack: false, title: '' }, one)).toBe('bar')
  })
  it('scatter needs a numeric x column', () => {
    const points = [{ h: 1, w: 2 }, { h: 2, w: 5 }, { h: 3, w: 4 }]
    const long = [
      { m: '2026-01', c: '数码', v: 10 },
      { m: '2026-01', c: '家居', v: 4 },
      { m: '2026-02', c: '数码', v: 12 },
    ]
    const pivoted = chartModel({ type: 'line', x: 'm', series: 'c', y: ['v'], stack: false, title: '' }, long)
    expect(pivoted?.series).toEqual(['数码', '家居'])
    expect(pivoted?.data.map((d) => d.values)).toEqual([[10, 4], [12, 0]])
    expect(availableKinds(pivoted!)).toContain('stacked')
    expect(chartModel({ type: 'line', x: 'm', series: 'c', y: ['v'], stack: false, title: '' }, [long[0]!, long[2]!])).toBeNull()
    const model = chartModel({ type: 'scatter', x: 'h', y: ['w'], stack: false, title: '' }, points)
    expect(model && availableKinds(model)).toContain('scatter')
    expect(chartModel({ type: 'scatter', x: 'k', y: ['n'], stack: false, title: '' }, rows)).toBeNull()
  })
})

describe('formatSpan', () => {
  it('reads long evaluation times in minutes and hours', () => {
    expect(formatSpan(458)).toBe('458 ms')
    expect(formatSpan(77_200)).toBe('1 分 17 秒')
    expect(formatSpan(740_600)).toBe('12 分钟')
    expect(formatSpan(25 * 60_000)).toBe('25 分钟')
    expect(formatSpan(65 * 60_000)).toBe('1 小时 5 分')
    expect(formatSpan(120 * 60_000)).toBe('2 小时')
  })
})

describe('niceScale', () => {
  it('keeps whole-number data on whole-number ticks', () => {
    expect(niceScale([2, 2]).ticks).toEqual([0, 1, 2])
    expect(niceScale([3, 7, 9]).ticks.every(Number.isInteger)).toBe(true)
    expect(niceScale([6345, 4490, 164]).ticks).toEqual([0, 2000, 4000, 6000, 8000])
  })
  it('still uses fractional steps for fractional data', () => {
    expect(niceScale([0.42, 0.33]).ticks).toEqual([0, 0.2, 0.4, 0.6])
  })
})

describe('eval comparison summary', () => {
  it('says how the two results line up', async () => {
    const { compareSummary } = await import('./pages/EvalPage')
    const base = { expected_rows: [{ a: 1, b: 2 }], actual_rows: [{ a: 1 }], columns_match: true, order_only: false, truncated: false }
    expect(compareSummary({ ...base, expected_row_count: null, actual_row_count: null })).toBeNull()
    expect(compareSummary({ ...base, expected_row_count: 48, actual_row_count: 48, matched_rows: 48 })).toEqual({ tone: 'success', text: '48 行全部一致。' })
    expect(compareSummary({ ...base, expected_row_count: 48, actual_row_count: 50, matched_rows: 46 })?.text).toBe('期望 48 行，实际 50 行，其中 46 行一致；实际少了 2 行；实际多出 4 行。')
    expect(compareSummary({ ...base, expected_row_count: 3, actual_row_count: 3, matched_rows: 3, order_only: true })?.text).toBe('3 行内容一致，但行顺序不同（这道题要求按顺序）。')
    expect(compareSummary({ ...base, expected_row_count: 1, actual_row_count: 1, columns_match: false })?.text).toBe('列数不同：期望 2 列，实际 1 列。')
    expect(compareSummary({ ...base, expected_row_count: 1, actual_row_count: 1, truncated: true })?.tone).toBe('warning')
  })
})

describe('available fields', () => {
  it('lists only visible columns and prefers personal over team over platform semantics', () => {
    const scope = { datasource_id: 1, tables: { 'shop.orders': { columns: ['amount', 'id'] } } }
    const tables = [{ id: 7, datasource_id: 1, schema_name: 'shop', name: 'orders', comment: '订单', description: '', status: 'active', columns: [
      { id: 1, table_id: 7, name: 'id', data_type: 'bigint', comment: '订单号', description: '', is_pk: true, ordinal: 1, status: 'active' },
      { id: 2, table_id: 7, name: 'amount', data_type: 'numeric', comment: '金额', description: '', is_pk: false, ordinal: 2, status: 'active' },
      { id: 3, table_id: 7, name: 'secret', data_type: 'text', comment: '', description: '', is_pk: false, ordinal: 3, status: 'active' },
    ] }]
    const sem = (scope: 'platform' | 'team' | 'personal', alias: string) => ({ id: alias.length, datasource_id: 1, scope, target_type: 'column' as const, target_id: 2, aliases: [alias], description: alias, enum_mapping: {}, owner_team_id: null, owner_user_id: null, status: 'active' as const, version: 1, created_at: '', updated_at: '' })
    const [t] = buildFields(scope, tables as never, [sem('platform', '实付'), sem('personal', '我的金额'), sem('team', '团队金额')] as never)
    expect(t?.label).toBe('订单')
    expect(t?.fields.map((f) => f.name)).toEqual(['id', 'amount'])
    expect(t?.fields[1]).toMatchObject({ aliases: ['我的金额'], level: 'personal', type: 'numeric' })
    expect(t?.fields[0]).toMatchObject({ aliases: ['订单号'], level: null })
  })
})

describe('eval overview helpers', () => {
  const mk = (id: number, day: number, passed: number, done = 10, status = 'success') => ({ id, created_at: new Date(Date.UTC(2026, 0, day)).toISOString(), passed, done, case_count: 10, status, duration_ms: done * 1000, model_id: '1', note: '' })
  const now = Date.UTC(2026, 0, 31)
  it('computes latest pass rate, delta and 30-day count from finished runs', async () => {
    const { overviewStats } = await import('./lib/evalText')
    const s = overviewStats([mk(3, 30, 9), mk(2, 20, 8), mk(1, -20, 5), mk(4, 31, 0, 3, 'canceled')], now)
    expect(s.passRate).toBe(0.9)
    expect(s.deltaPt).toBe(10)
    expect(s.count30).toBe(3)
    expect(s.avgMs).toBe(10000)
    expect(overviewStats([], now)).toMatchObject({ latest: null, passRate: null, deltaPt: null, avgMs: null })
  })
  it('leaves partial retests out of the pass rate, delta, time and trend', async () => {
    const { overviewStats, trendRuns } = await import('./lib/evalText')
    const retest = { ...mk(5, 30, 1, 1), case_count: 1 }
    const s = overviewStats([retest, mk(3, 29, 9), mk(2, 20, 8)], now, 10)
    expect(s.latest?.id).toBe(3)
    expect(s.deltaPt).toBe(10)
    expect(s.avgMs).toBe(10000)
    expect(s.count30).toBe(3)
    expect(trendRuns([retest, mk(3, 29, 9), mk(2, 20, 8)], { last: 10 }, now, 10).map((r) => r.id)).toEqual([2, 3])
    // Before the bank has loaded every finished run counts.
    expect(overviewStats([retest, mk(3, 29, 9)], now).latest?.id).toBe(5)
  })
  it('selects trend runs by count or days, oldest first', async () => {
    const { trendRuns, formatPt, changeRatio } = await import('./lib/evalText')
    const runs = [mk(3, 30, 9), mk(2, 20, 8), mk(1, -20, 5)]
    expect(trendRuns(runs, { last: 2 }, now).map((r) => r.id)).toEqual([2, 3])
    expect(trendRuns(runs, { days: 30 }, now).map((r) => r.id)).toEqual([2, 3])
    expect(formatPt(3.1)).toBe('+3.1 pt')
    expect(formatPt(-2)).toBe('-2 pt')
    expect(changeRatio(100, 150)).toBe(50)
    expect(changeRatio(0, 5)).toBeNull()
  })
})

describe('starter questions', () => {
  it('uses sourced items and falls back to plain questions', () => {
    const items = [{ question: '上个月 GMV 是多少', kind: 'metric' as const, name: 'GMV' }]
    expect(starters({ questions: ['上个月 GMV 是多少'], items, source: 'model' })).toEqual(items)
    expect(starters({ questions: ['订单有哪些'], items: [], source: 'rules' })).toEqual([{ question: '订单有哪些' }])
    expect(starters(undefined)).toEqual([])
  })
})

describe('query diagnostics', () => {
  it('reads rule, stage and error type in plain words and points to the eval case', async () => {
    const { diagnosticOf, diagnosticText, evalRefText } = await import('./lib/diagnostic')
    const d = diagnosticOf({ stage: 'sql_guard', rule: 'function_not_allowed', subject: 'generate_series', error_type: 'SqlBlocked', eval_run_id: 12, eval_case_id: 'C06' })!
    expect(diagnosticText(d)).toBe('规则：函数不在允许列表（generate_series） · 环节：SQL 安全校验 · 错误类型：SqlBlocked')
    expect(evalRefText(d)).toBe('评测 #12 · C06')
    expect(evalRefText(null, { run_id: 3, case_id: 'A1' })).toBe('评测 #3 · A1')
    expect(evalRefText(null)).toBe('')
    expect(diagnosticOf([])).toBeNull()
    expect(diagnosticText({ rule: 'new_rule' })).toBe('规则：new_rule')
    expect(diagnosticText({ stage: 'execute', code: 'DATASOURCE_UNAVAILABLE', subject: 'sqlstate_08006', error_type: 'DatasourceUnavailable' })).toBe('原因：08006 · 环节：执行查询 · 错误类型：DatasourceUnavailable')
    expect(diagnosticText({ subject: 'bool_parsing', field: 'unanswerable' })).toBe('原因：bool_parsing，字段 unanswerable')
    expect(diagnosticText({ rule: 'unanswerable', retries: 1, retry_rules: ['sqlstate_42803'] })).toBe('规则：可见范围内无法回答 · 纠错 1 次后（此前：分组错误（42803））')
    expect(diagnosticText({ rule: 'unanswerable', retries: 2, retry_rules: ['system_table', 'SQL_EXECUTION_ERROR'] })).toBe(
      '规则：可见范围内无法回答 · 纠错 2 次后（此前：访问系统表、SQL_EXECUTION_ERROR）',
    )
  })
})
