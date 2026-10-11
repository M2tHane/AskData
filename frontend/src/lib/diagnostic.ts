/**
 * 查询失败或被拦截时后端记下的诊断：哪个环节、哪条规则、什么错误类型、关联哪道评测题。
 * 只有固定词表和类型名，不含 SQL、数据或看不到的表名字段名。
 */
export interface Diagnostic {
  stage?: string
  code?: string
  rule?: string
  /** 被拦下的 SQL 函数名（只有已知函数才记），或解析错误的类型。 */
  subject?: string
  error_type?: string
  retries?: number
  /** 每次纠错前撞到的规则（没有规则时是错误码），按先后顺序。 */
  retry_rules?: string[]
  eval_run_id?: number
  eval_case_id?: string
}

export const STAGE_NAME: Record<string, string> = {
  start: '开始之前',
  load_context: '读取对话上下文',
  visible_scope: '计算可见范围',
  retrieval: '检索语义与指标',
  clarification: '澄清判断',
  generate_sql: '生成 SQL',
  sql_guard: 'SQL 安全校验',
  execute: '执行查询',
  correction: '自动纠错',
  presentation: '生成图表与回答',
  record: '写入日志',
  evaluation: '评测执行',
}

/** Pipeline order. Stored timings come back in JSONB key order, which is not the run order. */
export const STAGE_ORDER = Object.keys(STAGE_NAME)

/** Sort stage names into the order the pipeline runs them; unknown names go last. */
export function inRunOrder(names: string[]): string[] {
  const rank = (name: string) => (STAGE_ORDER.includes(name) ? STAGE_ORDER.indexOf(name) : STAGE_ORDER.length)
  return [...names].sort((a, b) => rank(a) - rank(b))
}

export const RULE_NAME: Record<string, string> = {
  parse_error: 'SQL 无法解析',
  multi_statement: '包含多条语句',
  not_select: '不是只读查询',
  write_statement: '包含写入或建表语句',
  query_settings: '带有查询设置子句',
  parameter: '包含参数占位符',
  recursive_cte: '使用了递归查询（WITH RECURSIVE）',
  user_defined_type: '使用了自定义类型',
  dangerous_function: '调用了危险函数',
  function_not_allowed: '函数不在允许列表',
  qualified_function: '带前缀的函数调用',
  system_table: '访问系统表',
  cross_catalog: '跨库访问',
  table_not_visible: '用到了不可见的表',
  column_withheld: '用到了无权查看的字段',
  unresolved_column: '字段不存在',
  star_projection: '使用了 SELECT *',
  duplicate_output: '输出列重名',
  lineage_unknown: '无法确认输出列来源',
  row_filter_invalid: '行级权限配置无效',
  not_guarded: '未经安全校验',
  datasource_mismatch: '数据源不匹配',
  not_read_only: '数据源不是只读账号',
  cost_exceeded: '预估代价超过上限',
  write_request: '要求修改数据',
  unanswerable: '可见范围内无法回答',
  correction_out_of_scope: '纠错后仍越权',
}

export function diagnosticOf(value: unknown): Diagnostic | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Diagnostic) : null
}

/** 一行给管理员看的诊断，例如「规则：函数不在允许列表（generate_series）· 环节：SQL 安全校验」。 */
export function diagnosticText(d: Diagnostic): string {
  const parts: string[] = []
  if (d.rule) parts.push(`规则：${RULE_NAME[d.rule] ?? d.rule}${d.subject ? `（${d.subject}）` : ''}`)
  if (d.stage) parts.push(`环节：${STAGE_NAME[d.stage] ?? d.stage}`)
  if (d.error_type) parts.push(`错误类型：${d.error_type}`)
  if (d.retries) {
    const before = (d.retry_rules ?? []).map((r) => RULE_NAME[r] ?? r)
    parts.push(`纠错 ${d.retries} 次后${before.length ? `（此前：${before.join('、')}）` : ''}`)
  }
  return parts.join(' · ')
}

/** 「评测 #12 · C06」，不在评测里时为空。 */
export function evalRefText(d: Diagnostic | null, ref?: unknown): string {
  const r = (ref && typeof ref === 'object' ? ref : {}) as { run_id?: number; case_id?: string }
  const run = d?.eval_run_id ?? r.run_id
  const id = d?.eval_case_id ?? r.case_id
  return run || id ? `评测${run ? ` #${run}` : ''}${id ? ` · ${id}` : ''}` : ''
}
