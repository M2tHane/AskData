import { describe, expect, it } from 'vitest'
import type { TrendPoint } from '../api/types'
import { compactNumber, periodTotals } from './usageStats'

describe('compactNumber', () => {
  it('keeps small numbers and abbreviates large ones', () => {
    expect(compactNumber(0)).toBe('0')
    expect(compactNumber(999)).toBe('999')
    expect(compactNumber(8320)).toBe('8,320')
    expect(compactNumber(53_000)).toBe('5.3万')
    expect(compactNumber(12_400_000)).toBe('1240万')
    expect(compactNumber(2_000_000_000)).toBe('20亿')
  })
  it('rolls over to the next unit instead of printing 10000万', () => {
    expect(compactNumber(99_999_999)).toBe('1亿')
  })
})

describe('periodTotals', () => {
  const p = (calls: number, tokens_in: number, tokens_out: number) => ({ calls, tokens_in, tokens_out, date: '2026-10-01', key: 'a' }) as TrendPoint
  it('sums calls and tokens and averages per call', () => {
    expect(periodTotals([p(2, 100, 50), p(1, 30, 20)])).toEqual({ calls: 3, tokens: 200, perCall: 67 })
  })
  it('does not divide by zero', () => {
    expect(periodTotals([])).toEqual({ calls: 0, tokens: 0, perCall: 0 })
  })
})
