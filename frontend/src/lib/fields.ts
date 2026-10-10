import type { PublicScope, SemanticView, TableRead } from '../api/types'
import type { ScopeLevel } from '../api/types'

export interface FieldInfo {
  table: string
  name: string
  type: string
  /** 别名：取生效的语义别名，没有时用字段注释。 */
  aliases: string[]
  description: string
  enums: Record<string, string>
  /** 生效的语义来自哪一级；没有语义时为 null。 */
  level: ScopeLevel | null
}

export interface TableInfo {
  key: string
  name: string
  /** 中文名：表的语义别名或注释。 */
  label: string
  fields: FieldInfo[]
}

const RANK: Record<string, number> = { personal: 3, team: 2, platform: 1 }

/** 同一个对象有多级语义时，个人级 > 团队级 > 平台级。 */
function effective(items: SemanticView[]): SemanticView | undefined {
  return items.filter((s) => s.status !== 'pending').sort((a, b) => (RANK[String(b.scope)] ?? 0) - (RANK[String(a.scope)] ?? 0))[0]
}

/**
 * 可用字段：只列当前用户可见（scope）的表和字段，类型和注释取自表结构，
 * 别名、说明、枚举值和级别取自生效的语义。
 */
export function buildFields(scope: PublicScope | undefined, tables: TableRead[] | undefined, semantics: SemanticView[] | undefined): TableInfo[] {
  if (!scope) return []
  const byKey = new Map((tables ?? []).map((t) => [`${t.schema_name}.${t.name}`, t]))
  const sem = semantics ?? []
  const of = (type: 'table' | 'column', id: number | undefined) => (id == null ? undefined : effective(sem.filter((s) => s.target_type === type && s.target_id === id)))
  return Object.entries(scope.tables)
    .map(([key, visible]) => {
      const table = byKey.get(key) ?? byKey.get(key.split('.').pop() ?? key)
      const tsem = of('table', table?.id)
      const cols = new Map((table?.columns ?? []).map((c) => [c.name, c]))
      const order = (name: string) => cols.get(name)?.ordinal ?? 9999
      const fields = [...visible.columns]
        .sort((a, b) => order(a) - order(b))
        .map((name): FieldInfo => {
          const col = cols.get(name)
          const s = of('column', col?.id)
          return {
            table: table?.name ?? key,
            name,
            type: col?.data_type ?? '',
            aliases: s?.aliases?.length ? s.aliases : col?.comment ? [col.comment] : [],
            description: s?.description || col?.description || col?.comment || '',
            enums: (s?.enum_mapping as Record<string, string> | undefined) ?? {},
            level: (s?.scope as ScopeLevel | undefined) ?? null,
          }
        })
      return { key, name: table?.name ?? key, label: tsem?.aliases?.[0] || table?.comment || '', fields }
    })
    .sort((a, b) => a.name.localeCompare(b.name))
}
