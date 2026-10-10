import { describe, expect, it } from 'vitest'
import type { GrantRead, TableRead } from '../api/types'
import { buildGrid, gridToMemberPayload, gridToTeamPayload } from '../lib/grid'

const tables = [
  { id: 1, name: 'orders', columns: [{ id: 10, name: 'amount' }, { id: 11, name: 'phone' }] },
  { id: 2, name: 'secrets', columns: [{ id: 20, name: 'token' }] },
] as unknown as TableRead[]

const grants: GrantRead = {
  datasource_id: 1,
  team_id: 5,
  tables: [
    {
      table_id: 1,
      member_visible: true,
      columns: [
        { column_id: 10, member_visible: true, row_filters: [{ column: 'region', operator: 'eq', value: '华东' }] },
        { column_id: 11, member_visible: false, mask_rule: { kind: 'phone', prefix: 3, suffix: 4 }, row_filters: [] },
      ],
    },
  ],
}

describe('permission grid', () => {
  it('treats tables absent from the grant as not authorized (default deny)', () => {
    const grid = buildGrid(tables, grants)
    expect(grid[1]?.granted).toBe(true)
    expect(grid[2]?.granted).toBe(false)
    expect(grid[2]?.columns[20]?.granted).toBe(false)
  })
  it('round-trips masks and row filters when saving team grants', () => {
    const payload = gridToTeamPayload(buildGrid(tables, grants))
    expect(payload).toHaveLength(1)
    const cols = payload[0]?.columns ?? []
    expect(cols[0]?.row_filters).toEqual([{ column: 'region', operator: 'eq', value: '华东' }])
    expect(cols[1]?.mask_rule).toEqual({ kind: 'phone', prefix: 3, suffix: 4 })
  })
  it('member payload only lists member-visible columns of member-visible tables', () => {
    expect(gridToMemberPayload(buildGrid(tables, grants))).toEqual([{ table_id: 1, column_ids: [10] }])
  })
  it('revoking a table drops it from the payload', () => {
    const grid = buildGrid(tables, grants)
    const table = grid[1]
    if (table) table.granted = false
    expect(gridToTeamPayload(grid)).toEqual([])
  })
})

import { setTableVisible, visibleCounts } from '../lib/grid'

describe('employee visibility switches', () => {
  it('turning a table on with no visible columns exposes all authorized columns', () => {
    const grid = buildGrid(tables, grants)
    const t = grid[1]
    if (t) {
      t.member_visible = false
      for (const c of Object.values(t.columns)) c.member_visible = false
    }
    setTableVisible(grid, 1, true)
    expect(visibleCounts(grid[1]!)).toEqual({ visible: 2, granted: 2 })
  })
  it('turning a table off hides it regardless of column flags', () => {
    const grid = buildGrid(tables, grants)
    setTableVisible(grid, 1, false)
    expect(visibleCounts(grid[1]!).visible).toBe(0)
    expect(gridToMemberPayload(grid)).toEqual([])
  })
  it('cannot expose a table the team has not been granted', () => {
    const grid = buildGrid(tables, grants)
    setTableVisible(grid, 2, true)
    expect(grid[2]?.member_visible).toBe(false)
  })
})

import { looksUnprovisioned, reasonText } from './EvalPage'

describe('eval page helpers', () => {
  it('flags runs where nothing reached the model', () => {
    expect(looksUnprovisioned([{ reason: 'execution_failed', tokens_in: 0, tokens_out: 0 }])).toBe(true)
    expect(looksUnprovisioned([{ reason: 'execution_failed', tokens_in: 10, tokens_out: 2 }])).toBe(false)
    expect(looksUnprovisioned([])).toBe(false)
  })
  it('explains a failed verdict with what actually happened', () => {
    expect(reasonText({ reason: 'status_mismatch', actual_status: 'clarify' })).toBe('应返回结果，实际要求澄清')
    expect(reasonText({ reason: 'safety_mismatch', actual_status: 'success' })).toBe('应被拦截，实际返回了结果')
    expect(reasonText({ reason: 'execution_failed', error: 'LLM_UNAVAILABLE' })).toBe('执行失败：模型暂时不可用')
  })
})

import { setAll, setColumnEmployee, setColumnTeam, setTableEmployee, setTableTeam, tableEmployeeState, tableTeamState } from '../lib/grid'

describe('datasource visibility toggles', () => {
  it('keeps employee-visible a subset of team-visible', () => {
    const grid = buildGrid(tables, undefined)
    setColumnEmployee(grid, 1, 10, true)
    expect(grid[1]?.columns[10]).toMatchObject({ granted: true, member_visible: true })
    expect(grid[1]?.granted).toBe(true)
    expect(tableTeamState(grid[1]!)).toBe('some')
    setColumnTeam(grid, 1, 10, false)
    expect(grid[1]?.columns[10]).toMatchObject({ granted: false, member_visible: false })
    expect(grid[1]?.granted).toBe(false)
    expect(grid[1]?.member_visible).toBe(false)
  })
  it('table switches cascade to every column', () => {
    const grid = buildGrid(tables, undefined)
    setTableEmployee(grid, 1, true)
    expect(tableTeamState(grid[1]!)).toBe('on')
    expect(tableEmployeeState(grid[1]!)).toBe('on')
    setTableTeam(grid, 1, false)
    expect(tableEmployeeState(grid[1]!)).toBe('off')
    expect(gridToTeamPayload(grid)).toEqual([])
  })
  it('select-all grants everything and clear revokes it', () => {
    const grid = buildGrid(tables, undefined)
    setAll(grid, true)
    expect(gridToMemberPayload(grid)).toEqual([{ table_id: 1, column_ids: [10, 11] }, { table_id: 2, column_ids: [20] }])
    setAll(grid, false)
    expect(gridToTeamPayload(grid)).toEqual([])
  })
})

import { buildRows, planChanges, splitAliases } from './SemanticPage'

describe('semantic batch editing', () => {
  const table = { id: 1, name: 'orders', columns: [{ id: 10, name: 'amount', data_type: 'numeric' }, { id: 11, name: 'phone', data_type: 'text' }] } as unknown as TableRead
  const existing = [{ id: 5, target_type: 'column', target_id: 10, scope: 'team', aliases: ['销售额'], description: '金额', status: 'team' }] as never[]
  it('lists the table and every column and prefills same-level entries only', () => {
    const rows = buildRows(table, 'team', existing)
    expect(rows.map((r) => r.name)).toEqual(['orders（表）', 'amount', 'phone'])
    expect(rows[1]).toMatchObject({ alias: '销售额', description: '金额' })
    const personal = buildRows(table, 'personal', existing)[1]
    expect(personal).toMatchObject({ alias: '', existing: undefined })
    // 其他级别的定义作为只读参考带出来。
    expect(personal?.inherited.map((s) => s.id)).toEqual([5])
  })
  it('saves filled rows and removes existing rows that were cleared', () => {
    const rows = buildRows(table, 'team', existing)
    rows[1] = { ...rows[1]!, alias: '', description: '' }
    rows[2] = { ...rows[2]!, alias: '手机号、电话' }
    const plan = planChanges(rows)
    expect(plan.save.map((r) => r.name)).toEqual(['phone'])
    expect(plan.remove.map((r) => r.name)).toEqual(['amount'])
    expect(splitAliases('手机号、电话, 号码')).toEqual(['手机号', '电话', '号码'])
  })
})

import { memberTableState, setMemberColumn, setMemberTable } from '../lib/grid'

describe('leader employee visibility only touches granted objects', () => {
  const grid = () => ({
    1: {
      granted: true,
      member_visible: true,
      columns: {
        10: { granted: true, member_visible: true, row_filters: [] },
        11: { granted: true, member_visible: false, row_filters: [] },
        12: { granted: false, member_visible: false, row_filters: [] },
      },
    },
    2: { granted: false, member_visible: false, columns: { 20: { granted: false, member_visible: false, row_filters: [] } } },
  })
  it('never exposes ungranted columns or tables', () => {
    const g = grid()
    setMemberTable(g, 1, true)
    expect(g[1].columns[12].member_visible).toBe(false)
    setMemberTable(g, 2, true)
    expect(g[2].member_visible).toBe(false)
    setMemberColumn(g, 1, 12, true)
    expect(g[1].columns[12].member_visible).toBe(false)
  })
  it('derives the table state from granted columns and hides the table when none remain', () => {
    const g = grid()
    expect(memberTableState(g[1])).toBe('some')
    setMemberColumn(g, 1, 11, true)
    expect(memberTableState(g[1])).toBe('on')
    setMemberTable(g, 1, false)
    expect(memberTableState(g[1])).toBe('off')
    expect(g[1].member_visible).toBe(false)
    setMemberColumn(g, 1, 10, true)
    expect(g[1].member_visible).toBe(true)
    setMemberColumn(g, 1, 10, false)
    expect(g[1].member_visible).toBe(false)
  })
})
