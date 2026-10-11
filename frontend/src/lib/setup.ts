import type { SetupRead, SetupSummary, SetupWrite, TableRead } from '../api/types'

/** 数据源接入设置的草稿：哪些表导入，每个字段和表的语义。 */
export interface SetupState {
  include: Record<number, boolean>
  columns: Record<number, string>
  tables: Record<number, string>
}

const clean = (s: string | null | undefined) => (s ?? '').replace(/\s+/g, ' ').trim()

/** 语义默认取已保存的说明，没有就用数据库注释。 */
export function initialState(read: SetupRead | undefined): SetupState {
  const state: SetupState = { include: {}, columns: {}, tables: {} }
  for (const t of read?.tables ?? []) {
    state.include[t.id] = t.status !== 'excluded'
    state.tables[t.id] = clean(t.description) || clean(t.comment)
    for (const c of t.columns ?? []) state.columns[c.id] = clean(c.description) || clean(c.comment)
  }
  return state
}

/** 已选的表里还没有语义的字段，按表计数。 */
export function missing(read: SetupRead | undefined, state: SetupState): { total: number; byTable: Record<number, number> } {
  const byTable: Record<number, number> = {}
  let total = 0
  for (const t of read?.tables ?? []) {
    if (!state.include[t.id]) continue
    const n = (t.columns ?? []).filter((c) => !clean(state.columns[c.id])).length
    if (n) byTable[t.id] = n
    total += n
  }
  return { total, byTable }
}

export const missingIds = (read: SetupRead | undefined, state: SetupState): number[] =>
  (read?.tables ?? []).filter((t) => state.include[t.id]).flatMap((t) => (t.columns ?? []).filter((c) => !clean(state.columns[c.id])).map((c) => c.id))

export function setupPayload(read: SetupRead | undefined, state: SetupState, publish: boolean): SetupWrite {
  return {
    publish,
    tables: (read?.tables ?? []).map((t) => ({
      table_id: t.id,
      include: !!state.include[t.id],
      description: clean(state.tables[t.id]),
      columns: (t.columns ?? []).map((c) => ({ column_id: c.id, description: clean(state.columns[c.id]) })),
    })),
  }
}

export const sameState = (a: SetupState, b: SetupState) => JSON.stringify(a) === JSON.stringify(b)

/** 表在设置页里的状态。 */
export type TableStage = 'imported' | 'new' | 'excluded'
export const stageOf = (t: TableRead): TableStage => (t.status === 'active' ? 'imported' : t.status === 'excluded' ? 'excluded' : 'new')

/** 数据源列表上给管理员的提示；null 表示没什么要做。 */
export function setupNotice(s: SetupSummary | undefined): { tone: 'warning' | 'info'; label: string } | null {
  if (!s) return null
  if (s.require_semantics && s.tables === 0 && s.pending_tables > 0) return { tone: 'warning', label: '待完善' }
  // 两项都有时用短文案，放得进状态列。
  if (s.pending_columns && s.pending_tables) return { tone: 'warning', label: `${s.pending_columns} 个字段、${s.pending_tables} 张新表待处理` }
  if (s.pending_columns) return { tone: 'warning', label: `${s.pending_columns} 个字段待补充语义` }
  if (s.pending_tables) return { tone: 'warning', label: `${s.pending_tables} 张新表待导入` }
  if (s.undocumented_columns) return { tone: 'info', label: `${s.undocumented_columns} 个字段没有语义` }
  return null
}
