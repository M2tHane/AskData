import type { ColumnGrantWrite, GrantRead, MaskRule, TableGrantWrite, TableRead } from '../api/types'

export type ColState = { granted: boolean; member_visible: boolean; mask_rule?: MaskRule | null; row_filters: ColumnGrantWrite['row_filters'] }
export type TableState = { granted: boolean; member_visible: boolean; columns: Record<number, ColState> }
export type Grid = Record<number, TableState>

export function buildGrid(tables: TableRead[], grants: GrantRead | undefined): Grid {
  const byTable = new Map<number, TableGrantWrite>((grants?.tables ?? []).map((t) => [t.table_id, t]))
  const grid: Grid = {}
  for (const t of tables) {
    const g = byTable.get(t.id)
    const byCol = new Map<number, ColumnGrantWrite>((g?.columns ?? []).map((c) => [c.column_id, c]))
    grid[t.id] = {
      granted: !!g,
      member_visible: g?.member_visible ?? false,
      columns: Object.fromEntries(
        (t.columns ?? []).map((c) => {
          const cg = byCol.get(c.id)
          return [c.id, { granted: !!cg, member_visible: cg?.member_visible ?? false, mask_rule: cg?.mask_rule ?? null, row_filters: cg?.row_filters ?? [] }]
        }),
      ),
    }
  }
  return grid
}

/** 表级「员工可见」联动字段：打开时若没有任何字段可见，则默认打开全部已授权字段；关闭时整表对员工隐藏。 */
export function setTableVisible(grid: Grid, tableId: number, visible: boolean): void {
  const t = grid[tableId]
  if (!t || !t.granted) return
  t.member_visible = visible
  if (visible && !Object.values(t.columns).some((c) => c.granted && c.member_visible)) {
    for (const c of Object.values(t.columns)) if (c.granted) c.member_visible = true
  }
}

export function visibleCounts(t: TableState): { visible: number; granted: number } {
  const cols = Object.values(t.columns).filter((c) => c.granted)
  return { visible: t.member_visible ? cols.filter((c) => c.member_visible).length : 0, granted: cols.length }
}

export function gridToTeamPayload(grid: Grid): TableGrantWrite[] {
  return Object.entries(grid)
    .filter(([, t]) => t.granted)
    .map(([id, t]) => ({
      table_id: Number(id),
      member_visible: t.member_visible,
      columns: Object.entries(t.columns)
        .filter(([, c]) => c.granted)
        .map(([cid, c]) => ({ column_id: Number(cid), member_visible: c.member_visible, mask_rule: c.mask_rule ?? null, row_filters: c.row_filters })),
    }))
}

export function gridToMemberPayload(grid: Grid): { table_id: number; column_ids: number[] }[] {
  return Object.entries(grid)
    .filter(([, t]) => t.granted && t.member_visible)
    .map(([id, t]) => ({
      table_id: Number(id),
      column_ids: Object.entries(t.columns).filter(([, c]) => c.granted && c.member_visible).map(([cid]) => Number(cid)),
    }))
}


const columnsOf = (t: TableState) => Object.values(t.columns)

/** 表级「团队可见」：勾选时整表所有字段都对团队可见；取消时整表（含员工可见）一并收回。 */
export function setTableTeam(grid: Grid, tableId: number, on: boolean): void {
  const t = grid[tableId]
  if (!t) return
  t.granted = on
  for (const c of columnsOf(t)) {
    c.granted = on
    if (!on) c.member_visible = false
  }
  if (!on) t.member_visible = false
}

/** 字段级「团队可见」：勾选会让所在表对团队可见；取消同时取消该字段的员工可见。 */
export function setColumnTeam(grid: Grid, tableId: number, columnId: number, on: boolean): void {
  const t = grid[tableId]
  const c = t?.columns[columnId]
  if (!t || !c) return
  c.granted = on
  if (on) t.granted = true
  else c.member_visible = false
  if (!columnsOf(t).some((x) => x.granted)) {
    t.granted = false
    t.member_visible = false
  }
  if (!columnsOf(t).some((x) => x.member_visible)) t.member_visible = false
}

/** 表级「员工可见」：勾选时整表字段都对团队和员工可见；取消时整表对员工隐藏。 */
export function setTableEmployee(grid: Grid, tableId: number, on: boolean): void {
  const t = grid[tableId]
  if (!t) return
  if (on) {
    t.granted = true
    t.member_visible = true
    for (const c of columnsOf(t)) c.granted = c.member_visible = true
  } else {
    t.member_visible = false
    for (const c of columnsOf(t)) c.member_visible = false
  }
}

/** 字段级「员工可见」：员工可见必须先团队可见，所以勾选会一并勾选团队可见；员工可见 ⊆ 团队可见。 */
export function setColumnEmployee(grid: Grid, tableId: number, columnId: number, on: boolean): void {
  const t = grid[tableId]
  const c = t?.columns[columnId]
  if (!t || !c) return
  if (on) {
    c.granted = c.member_visible = true
    t.granted = t.member_visible = true
  } else {
    c.member_visible = false
    if (!columnsOf(t).some((x) => x.member_visible)) t.member_visible = false
  }
}

export type CheckState = 'on' | 'off' | 'some'

const state = (flags: boolean[]): CheckState => (flags.length && flags.every(Boolean) ? 'on' : flags.some(Boolean) ? 'some' : 'off')

export const tableTeamState = (t: TableState): CheckState => state(columnsOf(t).map((c) => t.granted && c.granted))
export const tableEmployeeState = (t: TableState): CheckState => state(columnsOf(t).map((c) => t.granted && t.member_visible && c.granted && c.member_visible))

/** 新建团队授权的快捷操作：全部对团队和员工可见，或全部收回。 */
export function setAll(grid: Grid, on: boolean): void {
  for (const id of Object.keys(grid)) {
    setTableTeam(grid, Number(id), on)
    if (on) setTableEmployee(grid, Number(id), true)
  }
}

/** 负责人只能在团队已获授权的范围内决定员工可见：未授权的字段不会被改动。 */
export function setMemberTable(grid: Grid, tableId: number, on: boolean): void {
  const t = grid[tableId]
  if (!t || !t.granted) return
  t.member_visible = on
  for (const c of columnsOf(t)) if (c.granted) c.member_visible = on
}

export function setMemberColumn(grid: Grid, tableId: number, columnId: number, on: boolean): void {
  const t = grid[tableId]
  const c = t?.columns[columnId]
  if (!t || !c || !t.granted || !c.granted) return
  c.member_visible = on
  if (on) t.member_visible = true
  else if (!columnsOf(t).some((x) => x.granted && x.member_visible)) t.member_visible = false
}

export const memberTableState = (t: TableState): CheckState =>
  state(columnsOf(t).filter((c) => c.granted).map((c) => t.member_visible && c.member_visible))
