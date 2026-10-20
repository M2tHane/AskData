import type { VisibilityRead } from '../api/types'

/** 管理员编辑中的层级可见范围草稿：数据源 → 表 → 字段，未自定义的向上继承。 */
export interface VColumn {
  custom: boolean
  teams: number[]
}
export interface VTable {
  custom: boolean
  teams: number[]
  columns: Record<number, VColumn>
}
export interface VState {
  teams: number[]
  tables: Record<number, VTable>
}
export interface Effective {
  tables: Record<number, number[]>
  columns: Record<number, number[]>
}

export function fromRead(read: VisibilityRead): VState {
  return {
    teams: [...read.team_ids],
    tables: Object.fromEntries(
      read.tables.map((t) => [
        t.table_id,
        {
          custom: t.custom,
          teams: [...t.team_ids],
          columns: Object.fromEntries(t.columns.map((c) => [c.column_id, { custom: c.custom, teams: [...c.team_ids] }])),
        },
      ]),
    ),
  }
}

const within = (ids: number[], parent: number[]) => ids.filter((id) => parent.includes(id))

/** 与后端 permission/visibility.py 的解析规则一致：自定义对象的团队不会超出上级。 */
export function resolve(state: VState): Effective {
  const tables: Record<number, number[]> = {}
  const columns: Record<number, number[]> = {}
  for (const [tid, t] of Object.entries(state.tables)) {
    const eff = t.custom ? within(t.teams, state.teams) : [...state.teams]
    tables[Number(tid)] = eff
    for (const [cid, c] of Object.entries(t.columns)) columns[Number(cid)] = c.custom ? within(c.teams, eff) : [...eff]
  }
  return { tables, columns }
}

export const isCustom = (s: VState) => Object.values(s.tables).some((t) => t.custom || Object.values(t.columns).some((c) => c.custom))

export function setDatasourceTeams(s: VState, ids: number[]): VState {
  return { ...s, teams: ids }
}

export function setTableTeams(s: VState, tableId: number, ids: number[]): VState {
  const t = s.tables[tableId]
  if (!t) return s
  return { ...s, tables: { ...s.tables, [tableId]: { ...t, custom: true, teams: ids } } }
}

/** 恢复为继承：表自己和它下面的字段一并恢复。 */
export function inheritTable(s: VState, tableId: number): VState {
  const t = s.tables[tableId]
  if (!t) return s
  const columns = Object.fromEntries(Object.entries(t.columns).map(([id, c]) => [id, { ...c, custom: false }]))
  return { ...s, tables: { ...s.tables, [tableId]: { ...t, custom: false, columns } } }
}

export function setColumnTeams(s: VState, tableId: number, columnId: number, ids: number[]): VState {
  const t = s.tables[tableId]
  if (!t?.columns[columnId]) return s
  return { ...s, tables: { ...s.tables, [tableId]: { ...t, columns: { ...t.columns, [columnId]: { custom: true, teams: ids } } } } }
}

export function inheritColumn(s: VState, tableId: number, columnId: number): VState {
  const t = s.tables[tableId]
  const c = t?.columns[columnId]
  if (!t || !c) return s
  return { ...s, tables: { ...s.tables, [tableId]: { ...t, columns: { ...t.columns, [columnId]: { ...c, custom: false } } } } }
}

export function resetAll(s: VState): VState {
  return {
    ...s,
    tables: Object.fromEntries(
      Object.entries(s.tables).map(([id, t]) => [id, { custom: false, teams: t.teams, columns: Object.fromEntries(Object.entries(t.columns).map(([cid, c]) => [cid, { ...c, custom: false }])) }]),
    ),
  }
}

/** 保存用的请求体：只带自定义的表和字段，其余按继承处理；自定义对象按有效团队提交。 */
export function toWrite(s: VState) {
  const eff = resolve(s)
  return {
    team_ids: [...s.teams].sort((a, b) => a - b),
    tables: Object.entries(s.tables)
      .filter(([, t]) => t.custom || Object.values(t.columns).some((c) => c.custom))
      .map(([tid, t]) => ({
        table_id: Number(tid),
        custom: t.custom,
        team_ids: t.custom ? [...(eff.tables[Number(tid)] ?? [])].sort((a, b) => a - b) : [],
        columns: Object.entries(t.columns)
          .filter(([, c]) => c.custom)
          .map(([cid]) => ({ column_id: Number(cid), team_ids: [...(eff.columns[Number(cid)] ?? [])].sort((a, b) => a - b) })),
      })),
  }
}

export const sameState = (a: VState, b: VState) => JSON.stringify(toWrite(a)) === JSON.stringify(toWrite(b))
