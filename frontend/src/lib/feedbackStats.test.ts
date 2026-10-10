import { describe, expect, it } from 'vitest'
import { badRate, isEvalConverted, snapshotSql, topReason } from './feedbackStats'

describe('feedbackStats', () => {
  it('picks the most common reason among downvotes only', () => {
    expect(
      topReason([
        { rating: 'up', reason_code: 'other' },
        { rating: 'down', reason_code: 'metric_wrong' },
        { rating: 'down', reason_code: 'table_wrong' },
        { rating: 'down', reason_code: 'metric_wrong' },
        { rating: 'down', reason_code: null },
      ]),
    ).toEqual({ code: 'metric_wrong', count: 2 })
    expect(topReason([{ rating: 'up', reason_code: 'other' }])).toBeNull()
  })
  it('computes rate only with queries', () => {
    expect(badRate(2, 40)).toBe(0.05)
    expect(badRate(2, 0)).toBeNull()
  })
  it('reads converted kind and snapshot sql', () => {
    expect(isEvalConverted({ converted_ref: 'eval:3' })).toBe(true)
    expect(isEvalConverted({ converted_ref: 'example:3' })).toBe(false)
    expect(snapshotSql({ snapshot: { normalized_sql: 'select 1' } })).toBe('select 1')
    expect(snapshotSql({ snapshot: {} })).toBe('')
  })
})
