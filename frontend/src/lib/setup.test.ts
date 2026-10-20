import { describe, expect, it } from 'vitest'
import type { SetupRead, SetupSummary } from '../api/types'
import { initialState, missing, missingIds, sameState, setupNotice, setupPayload, stageOf } from './setup'

const col = (id: number, name: string, comment = '', description = '', status = 'active') => ({ id, table_id: 0, name, data_type: 'text', comment, description, is_pk: false, ordinal: id, status })
const read = {
  datasource_id: 1,
  require_semantics: true,
  tables: [
    { id: 1, datasource_id: 1, schema_name: 'public', name: 'orders', comment: '订单', description: '', status: 'pending', synced_at: '', columns: [col(11, 'id', '订单号'), col(12, 'memo', '', '', 'pending'), col(13, 'amount', '实付', '实付金额（元）')] },
    { id: 2, datasource_id: 1, schema_name: 'public', name: 'logs', comment: '', description: '', status: 'excluded', synced_at: '', columns: [col(21, 'payload', '', '', 'pending')] },
    { id: 3, datasource_id: 1, schema_name: 'public', name: 'customers', comment: '', description: '客户档案', status: 'active', synced_at: '', columns: [col(31, 'phone', '  手机号 ')] },
  ],
} as unknown as SetupRead

describe('datasource setup', () => {
  it('starts from saved semantics, falling back to database comments', () => {
    const s = initialState(read)
    expect(s.columns).toEqual({ 11: '订单号', 12: '', 13: '实付金额（元）', 21: '', 31: '手机号' })
    expect(s.tables).toEqual({ 1: '订单', 2: '', 3: '客户档案' })
    expect(s.include).toEqual({ 1: true, 2: false, 3: true })
  })

  it('counts only columns of tables that will be imported', () => {
    const s = initialState(read)
    expect(missing(read, s)).toEqual({ total: 1, byTable: { 1: 1 } })
    expect(missingIds(read, s)).toEqual([12])
    const all = { ...s, include: { ...s.include, 2: true } }
    expect(missing(read, all).total).toBe(2)
    expect(missing(read, { ...s, columns: { ...s.columns, 12: '   ' } }).total).toBe(1)
  })

  it('sends every table with its choice and trimmed semantics', () => {
    const s = initialState(read)
    const body = setupPayload(read, { ...s, columns: { ...s.columns, 12: '  订单 备注 ' } }, true)
    expect(body.publish).toBe(true)
    expect(body.tables?.[0]).toEqual({ table_id: 1, include: true, description: '订单', columns: [{ column_id: 11, description: '订单号' }, { column_id: 12, description: '订单 备注' }, { column_id: 13, description: '实付金额（元）' }] })
    expect(body.tables?.[1]?.include).toBe(false)
  })

  it('knows when nothing changed', () => {
    expect(sameState(initialState(read), initialState(read))).toBe(true)
    const s = initialState(read)
    expect(sameState(s, { ...s, include: { ...s.include, 1: false } })).toBe(false)
  })

  it('names each table stage', () => {
    expect(read.tables.map(stageOf)).toEqual(['new', 'excluded', 'imported'])
  })

  it('tells admins what each datasource still needs', () => {
    const base: SetupSummary = { datasource_id: 1, require_semantics: true, tables: 0, pending_tables: 4, pending_columns: 0, undocumented_columns: 0 }
    expect(setupNotice(base)).toEqual({ tone: 'warning', label: '待完善' })
    expect(setupNotice({ ...base, tables: 3, pending_tables: 1, pending_columns: 2 })?.label).toBe('2 个字段、1 张新表待处理')
    expect(setupNotice({ ...base, tables: 3, pending_tables: 1 })?.label).toBe('1 张新表待导入')
    expect(setupNotice({ ...base, require_semantics: false, tables: 3, pending_tables: 0, undocumented_columns: 5 })).toEqual({ tone: 'info', label: '5 个字段没有语义' })
    expect(setupNotice({ ...base, tables: 3, pending_tables: 0 })).toBeNull()
    expect(setupNotice(undefined)).toBeNull()
  })
})
