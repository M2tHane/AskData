import { describe, expect, it } from 'vitest'
import type { TrendPoint } from '../api/types'
import { compactNumber, periodTotals } from './usageStats'

describe('compactNumber', () => {
  it('keeps small numbers and abbreviates large ones', () => {
    expect(compactNumber(0)).toBe('0')
    expect(compactNumber(999)).toBe('999')
    expect(compactNumber(3200)).toBe('3.2K')
    expect(compactNumber(12_400_000)).toBe('12.4M')
    expect(compactNumber(2_000_000_000)).toBe('2B')
  })
  it('rolls over to the next unit instead of printing 1000.0K', () => {
    expect(compactNumber(999_950)).toBe('1M')
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
