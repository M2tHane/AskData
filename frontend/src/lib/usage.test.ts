import { describe, expect, it } from 'vitest'
import { alertsOnly, pivotTrend } from './usage'

const point = (date: string, key: string, tokens: number) => ({ date, key, calls: 1, tokens_in: tokens, tokens_out: 0 })

describe('pivotTrend', () => {
  it('fills missing days and merges small series into others', () => {
    const points = [point('2026-10-01', 'a', 5), point('2026-10-03', 'a', 7), point('2026-10-03', 'b', 1), point('2026-10-02', 'c', 2)]
    const table = pivotTrend(points, '2026-10-01', '2026-10-03', (k) => `团队${k}`, 2)
    expect(table.keys).toEqual(['团队a', '团队c', '其他'])
    expect(table.rows).toEqual([
      { date: '10-01', 团队a: 5, 团队c: 0, 其他: 0 },
      { date: '10-02', 团队a: 0, 团队c: 2, 其他: 0 },
      { date: '10-03', 团队a: 7, 团队c: 0, 其他: 1 },
    ])
  })
})

describe('alertsOnly', () => {
  it('keeps warning and exceeded entries', () => {
    const item = (level: 'ok' | 'warning' | 'exceeded') => ({ team_id: 1, kind: 'monthly_tokens' as const, used: 1, limit: 2, ratio: 0.5, level })
    expect(alertsOnly([item('ok'), item('warning'), item('exceeded')]).map((i) => i.level)).toEqual(['warning', 'exceeded'])
  })
})
