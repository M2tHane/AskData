/** 取出 SQL 的 WHERE 条件（到 GROUP/ORDER/HAVING/LIMIT 为止），用于空结果时回显实际筛选。 */
export function extractWhereClause(sql: string | null | undefined): string | null {
  if (!sql) return null
  const compact = sql.replace(/\s+/g, ' ').trim()
  const match = /\bWHERE\b(.+?)(?=\bGROUP BY\b|\bORDER BY\b|\bHAVING\b|\bLIMIT\b|\bUNION\b|\)\s*(?:AS\b|$)|;|$)/i.exec(compact)
  const where = match?.[1]?.trim()
  return where ? where : null
}
