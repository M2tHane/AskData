import type { ErrorCode } from '../api/types'

/** 发生了什么 + 原因代码 + 下一步。拦截回复只说明类别，不展示对象名称。 */
const TEXT: Record<ErrorCode, string> = {
  OK: '成功',
  VALIDATION_ERROR: '请求内容不合法，请检查输入后重试',
  INTERNAL_ERROR: '服务出错了，请稍后重试；持续出现请联系管理员',
  AUTH_FAILED: '用户名或密码不正确，或登录已失效，请重新登录',
  TOKEN_EXPIRED: '登录已过期，请重新登录',
  FORBIDDEN: '你没有权限执行该操作，请联系团队负责人',
  NOT_FOUND: '对象不存在，或你看不到它',
  CONFLICT: '与已有内容冲突，请刷新后重试',
  DATASOURCE_UNAVAILABLE: '数据源暂时连不上，请稍后重试或联系管理员',
  SQL_NOT_SELECT: '查询已拦截：只允许 SELECT 查询',
  SQL_MULTI_STATEMENT: '查询已拦截：包含多条语句，只允许单条 SELECT',
  SQL_FORBIDDEN_FUNC: '查询已拦截：使用了被禁用的函数',
  SQL_SYSTEM_TABLE: '查询已拦截：不允许访问系统表',
  SQL_OUT_OF_SCOPE: '查询已拦截：涉及你看不到的表或字段，可联系团队负责人申请',
  SQL_PARSE_FAILED: '查询已拦截：无法解析这条 SQL',
  SQL_COST_EXCEEDED: '查询已拦截：预估成本过高，请缩小时间范围或增加过滤条件',
  QUERY_TIMEOUT: '查询超时，请缩小范围后重试',
  QUERY_CANCELLED: '查询已取消',
  RATE_LIMITED: '请求太频繁，请稍后再试',
  LLM_UNAVAILABLE: '模型暂时不可用，请稍后重试',
  QUOTA_EXCEEDED: '今日查询额度已用完，请联系团队负责人',
  SQL_EXECUTION_ERROR: '查询执行失败，已尝试自动纠错仍未成功，请换个问法',
  TASK_FAILED: '后台任务失败，请查看任务详情',
  STORAGE_UNAVAILABLE: '文件存储暂时不可用，请稍后重试',
}

export function describeError(code: ErrorCode | string | undefined): string {
  if (!code) return TEXT.INTERNAL_ERROR
  const known = TEXT[code as ErrorCode]
  return known ? `${known}。原因：${code}` : `请求失败。原因：${code}`
}

export interface FriendlyError {
  title: string
  hint?: string
  code: string
  showCode: boolean
}

const FRIENDLY: Partial<Record<ErrorCode, { title: string; hint?: string }>> = {
  SQL_OUT_OF_SCOPE: { title: '问题涉及你看不到的表或字段', hint: '换个问法绕开它，或联系团队负责人开通。' },
  SQL_NOT_SELECT: { title: '只能查询数据，不能修改', hint: '这里只支持查询类问题，不支持新增、修改或删除。' },
  SQL_MULTI_STATEMENT: { title: '一次只能问一个查询', hint: '把多个问题拆开，一次问一个。' },
  SQL_FORBIDDEN_FUNC: { title: '查询用到了被禁用的函数', hint: '换个问法，不要要求调用外部函数或文件操作。' },
  SQL_SYSTEM_TABLE: { title: '不能查询系统表', hint: '只能查询已开放给你的业务数据。' },
  SQL_PARSE_FAILED: { title: '没能理解这条 SQL', hint: '检查语法后重试；手改 SQL 时只支持单条 SELECT。' },
  SQL_COST_EXCEEDED: { title: '这个查询太重了', hint: '缩小时间范围，或增加过滤条件后再试。' },
  SQL_EXECUTION_ERROR: { title: '查询执行失败', hint: '已自动纠错仍未成功，换个问法，或指明要看哪几列。' },
  QUERY_TIMEOUT: { title: '查询超时', hint: '缩小时间范围或减少分组后重试。' },
  QUOTA_EXCEEDED: { title: '今日查询额度已用完', hint: '联系团队负责人调整额度。' },
  RATE_LIMITED: { title: '提问太频繁', hint: '稍等几秒再试。' },
  LLM_UNAVAILABLE: { title: '模型暂时不可用', hint: '稍后重试；持续出现请联系管理员。' },
  DATASOURCE_UNAVAILABLE: { title: '数据源暂时连不上', hint: '稍后重试，或联系管理员检查数据源。' },
  FORBIDDEN: { title: '你没有权限这样做', hint: '联系团队负责人开通。' },
  NOT_FOUND: { title: '对象不存在，或你看不到它' },
  AUTH_FAILED: { title: '登录已失效', hint: '重新登录后再试。' },
  TOKEN_EXPIRED: { title: '登录已过期', hint: '重新登录后再试。' },
  QUERY_CANCELLED: { title: '已停止生成' },
  INTERNAL_ERROR: { title: '服务出错了', hint: '请稍后重试；持续出现请联系管理员。' },
  VALIDATION_ERROR: { title: '请求内容不合法', hint: '检查输入后重试。' },
}

/** 只有需要报给管理员排查的错误才显示错误码，其余只说人话。 */
const SHOW_CODE = new Set<string>(['INTERNAL_ERROR', 'TASK_FAILED', 'STORAGE_UNAVAILABLE', 'VALIDATION_ERROR'])

/** 标题 + 下一步 + 原因代码；拦截文案只说类别，不展示不可见对象名称。 */
export function friendlyError(code: ErrorCode | string | undefined): FriendlyError {
  const value = String(code ?? 'INTERNAL_ERROR')
  const known = FRIENDLY[value as ErrorCode]
  if (known) return { ...known, code: value, showCode: SHOW_CODE.has(value) }
  return { title: TEXT[value as ErrorCode] ?? '请求失败', code: value, showCode: true }
}

/** 后端把失败回答的正文存成错误码的英文说明（如 "Internal error"）；认出来以便显示成中文。 */
export function codeFromMessage(content: string): ErrorCode | undefined {
  const text = content.trim().toLowerCase()
  return (Object.keys(TEXT) as ErrorCode[]).find((code) => code !== 'OK' && code.replace(/_/g, ' ').toLowerCase() === text)
}

export class ApiError extends Error {
  readonly code: ErrorCode | string
  readonly status: number
  /** 后端回的 X-Request-ID，方便对照查询日志排查。 */
  readonly requestId?: string
  constructor(code: ErrorCode | string, status: number, requestId?: string) {
    super(describeError(code))
    this.code = code
    this.status = status
    this.requestId = requestId ?? undefined
  }
}
