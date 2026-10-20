import { create } from 'zustand'
import type { TableRead } from '../api/types'

/**
 * 同步后发现的字段变化。后端的同步任务不回传新增 / 删除明细，这里在同步前后各取一次表结构做对比，
 * 结果记在本机，直到管理员点「标记已处理」。改名会表现为一删一增。
 */
export interface SchemaChange {
  kind: 'add-table' | 'remove-table' | 'add-column' | 'remove-column'
  table: string
  column?: string
  /** 删除前的 id，用来找受影响的语义。 */
  id?: number
  type?: string
}

export interface ChangeSet {
  at: string
  changes: SchemaChange[]
}

export function diffTables(before: TableRead[], after: TableRead[]): SchemaChange[] {
  const out: SchemaChange[] = []
  const prev = new Map(before.map((t) => [t.name, t]))
  const next = new Map(after.map((t) => [t.name, t]))
  for (const [name, t] of next) {
    const old = prev.get(name)
    if (!old) {
      out.push({ kind: 'add-table', table: name, id: t.id })
      continue
    }
    const oldCols = new Map((old.columns ?? []).map((c) => [c.name, c]))
    const newCols = new Set((t.columns ?? []).map((c) => c.name))
    for (const c of t.columns ?? []) if (!oldCols.has(c.name)) out.push({ kind: 'add-column', table: name, column: c.name, id: c.id, type: c.data_type })
    for (const [cname, c] of oldCols) if (!newCols.has(cname)) out.push({ kind: 'remove-column', table: name, column: cname, id: c.id, type: c.data_type })
  }
  for (const [name, t] of prev) if (!next.has(name)) out.push({ kind: 'remove-table', table: name, id: t.id })
  return out
}

const KEY = 'askdata.schema-changes'

function load(): Record<number, ChangeSet> {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<number, ChangeSet>
  } catch {
    return {}
  }
}

interface ChangesState {
  byDatasource: Record<number, ChangeSet>
  record: (datasourceId: number, changes: SchemaChange[]) => void
  resolve: (datasourceId: number) => void
}

export const useSchemaChanges = create<ChangesState>((set, get) => {
  const persist = (byDatasource: Record<number, ChangeSet>) => {
    try {
      localStorage.setItem(KEY, JSON.stringify(byDatasource))
    } catch {
      /* not persisted */
    }
    set({ byDatasource })
  }
  return {
    byDatasource: load(),
    record: (datasourceId, changes) => {
      if (!changes.length) return
      const cur = get().byDatasource[datasourceId]
      // 未处理的变化累积起来，同一处只记一次。
      const merged = [...(cur?.changes ?? []), ...changes].filter((c, i, all) => all.findIndex((x) => x.kind === c.kind && x.table === c.table && x.column === c.column) === i)
      persist({ ...get().byDatasource, [datasourceId]: { at: new Date().toISOString(), changes: merged } })
    },
    resolve: (datasourceId) => {
      const next = { ...get().byDatasource }
      delete next[datasourceId]
      persist(next)
    },
  }
})

export const CHANGE_LABEL: Record<SchemaChange['kind'], string> = {
  'add-table': '新增表',
  'remove-table': '删除表',
  'add-column': '新增字段',
  'remove-column': '删除字段',
}
