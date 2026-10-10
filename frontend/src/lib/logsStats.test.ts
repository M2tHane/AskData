import { describe, expect, it } from 'vitest'
import { csvCell, logStats, stageBars, toCsv } from './logsStats'

describe('logsStats', () => {
  it('counts corrected successes and averages timed rows', () => {
    expect(
      logStats([
        { status: 'success', retry_count: 1, duration_ms: 2000 },
        { status: 'failed', retry_count: 2, duration_ms: 4000 },
        { status: 'clarify', retry_count: 0, duration_ms: 0 },
      ]),
    ).toEqual({ corrected: 1, avgMs: 3000 })
    expect(logStats([])).toEqual({ corrected: 0, avgMs: null })
  })
  it('escapes csv cells', () => {
    expect(csvCell('a,b')).toBe('"a,b"')
    expect(csvCell('say "hi"')).toBe('"say ""hi"""')
    expect(csvCell('=SUM(A1)')).toBe("'=SUM(A1)")
    expect(csvCell(12)).toBe('12')
    expect(csvCell(null)).toBe('')
    expect(toCsv(['a', 'b'], [[1, 'x']])).toBe('a,b\r\n1,x')
  })
  it('scales stage bars to the longest stage', () => {
    expect(stageBars({ a: 100, b: 50 }).map((b) => b.pct)).toEqual([100, 50])
    expect(stageBars(undefined)).toEqual([])
  })
})
