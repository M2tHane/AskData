import type { SchemaChange } from '../state/schemaChanges'

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
/** 标识符边界：前后不能是字母、数字、下划线。 */
const mentions = (sql: string, name: string) => new RegExp(`(^|[^\\w])${esc(name)}($|[^\\w])`, 'i').test(sql)

/**
 * 指标口径里引用了最近一次同步删掉的表或字段，返回被删掉的对象（「表.字段」或「表」）。
 * 只做文本匹配：删字段要求表名和字段名都出现在口径里，删表要求表名出现。
 */
export function removedReferences(sql: string, changes: readonly SchemaChange[]): string[] {
  const out: string[] = []
  for (const c of changes) {
    if (c.kind === 'remove-table' && mentions(sql, c.table)) out.push(c.table)
    if (c.kind === 'remove-column' && c.column && mentions(sql, c.column) && mentions(sql, c.table)) out.push(`${c.table}.${c.column}`)
  }
  return [...new Set(out)]
}
