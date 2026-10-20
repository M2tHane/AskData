import { describe, expect, it } from 'vitest'
import { removedReferences } from './metricsValidity'

describe('removedReferences', () => {
  const sql = 'SELECT SUM(paid_amount) / COUNT(*) FROM orders WHERE region = 1'
  it('flags a removed column only when both table and column are mentioned', () => {
    expect(removedReferences(sql, [{ kind: 'remove-column', table: 'orders', column: 'paid_amount' }])).toEqual(['orders.paid_amount'])
    expect(removedReferences(sql, [{ kind: 'remove-column', table: 'refunds', column: 'paid_amount' }])).toEqual([])
  })
  it('matches whole identifiers only and ignores additions', () => {
    expect(removedReferences(sql, [{ kind: 'remove-column', table: 'orders', column: 'paid' }])).toEqual([])
    expect(removedReferences(sql, [{ kind: 'add-column', table: 'orders', column: 'region' }])).toEqual([])
  })
  it('flags a removed table', () => {
    expect(removedReferences(sql, [{ kind: 'remove-table', table: 'ORDERS' }])).toEqual(['ORDERS'])
  })
})
