import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type {
  PublicScope,
  ChatLimits,
  ConversationView,
  CurrentUser,
  DatasourceChoice,
  DatasourceRead,
  DatasourceWrite,
  ConnectionTest,
  EvalCaseView,
  Evaluation,
  RunListItem,
  SuggestedQuestions,
  FeedbackInput,
  MyFeedback,
  FeedbackView,
  MessageView,
  MetricInput,
  MetricView,
  Page,
  PromotionView,
  QueryReply,
  QueryView,
  RunView,
  SemanticInput,
  SemanticView,
  TableRead,
  PersonRead,
  TokenPair,
  UploadJob,
  UploadParseConfig,
  UploadPreview,
} from './types'

export const timezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'

export const login = (username: string, password: string) =>
  api<TokenPair>('POST', '/api/auth/login', { body: { username, password } })
export const fetchMe = () => api<CurrentUser>('GET', '/api/auth/me')
export const logout = (refresh: string) => api<null>('POST', '/api/auth/logout', { body: { refresh_token: refresh } })

/* ---- chat ---- */
const CONVERSATION_PAGE = 50

/** 会话按最近活动排序，分页加载；q 按标题搜索。 */
export const useConversations = (q = '') =>
  useInfiniteQuery({
    queryKey: ['conversations', 'list', q],
    queryFn: ({ pageParam }) => api<Page<ConversationView>>('GET', '/api/chat/conversations', { query: { page: pageParam, page_size: CONVERSATION_PAGE, q } }),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page * last.page_size < last.total ? last.page + 1 : undefined),
    placeholderData: keepPreviousData,
  })

/** 当前打开的会话单独读取：不在列表已加载的页里（很早的会话、搜索中）也能打开。 */
export const useConversation = (id: number | null) =>
  useQuery({ queryKey: ['conversations', 'one', id], queryFn: () => api<ConversationView>('GET', `/api/chat/conversations/${id}`), enabled: id != null, retry: false })

export function useRenameConversation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { id: number; title: string }) => api<ConversationView>('PATCH', `/api/chat/conversations/${body.id}`, { body: { title: body.title } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['conversations'] }),
  })
}

export const useAvailableDatasources = () =>
  useQuery({ queryKey: ['datasources', 'available'], queryFn: () => api<Page<DatasourceChoice>>('GET', '/api/datasources/available', { query: { page_size: 100 } }) })

const MESSAGE_PAGE = 100

/** 最近的 limit 条消息（按时间正序）和总条数；超过时由用户点「显示更早的消息」加大 limit。 */
export async function fetchMessages(conversationId: number, limit = MESSAGE_PAGE): Promise<{ items: MessageView[]; total: number }> {
  const path = `/api/chat/conversations/${conversationId}/messages`
  const first = await api<Page<MessageView>>('GET', path, { query: { page: 1, page_size: MESSAGE_PAGE } })
  const pages = Math.ceil(first.total / MESSAGE_PAGE)
  if (pages <= 1) return { items: first.items, total: first.total }
  // 从最后一页往前取，直到够 limit 条；第一页已经有了就不再取。
  const wanted = Math.max(1, pages - Math.ceil(limit / MESSAGE_PAGE))
  const rest = await Promise.all(
    Array.from({ length: pages - wanted + 1 }, (_, i) => wanted + i)
      .filter((page) => page > 1)
      .map((page) => api<Page<MessageView>>('GET', path, { query: { page, page_size: MESSAGE_PAGE } })),
  )
  const items = [...(wanted === 1 ? first.items : []), ...rest.flatMap((p) => p.items)]
  return { items: items.slice(-limit), total: first.total }
}

/** 历史回答的结果不落库：打开时按当前权限重新读取。 */
export const useReplay = (conversationId: number, messageId: number, enabled: boolean) =>
  useQuery({
    queryKey: ['replay', conversationId, messageId],
    queryFn: () => api<QueryReply>('GET', `/api/chat/conversations/${conversationId}/messages/${messageId}/replay`),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  })

/** 结果预览与导出的上限，显示在结果卡片底部。 */
export const useChatLimits = () =>
  useQuery({ queryKey: ['chat-limits'], queryFn: () => api<ChatLimits>('GET', '/api/chat/limits'), staleTime: Infinity })

export const useMessages = (conversationId: number | null, limit = MESSAGE_PAGE) =>
  useQuery({ queryKey: ['messages', conversationId, limit], queryFn: () => fetchMessages(conversationId as number, limit), enabled: conversationId != null, placeholderData: keepPreviousData })

export function useCreateConversation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { datasource_id: number; title?: string }) => api<ConversationView>('POST', '/api/chat/conversations', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['conversations'] }),
  })
}

export function useDeleteConversation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<{ deleted: boolean }>('DELETE', `/api/chat/conversations/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['conversations'] }),
  })
}

/** sourceMessageId：被编辑的那条回答；后端沿用它的图表配置。 */
export const rerunSql = (conversationId: number, sql: string, sourceMessageId?: number | null) =>
  api<QueryReply>('POST', `/api/chat/conversations/${conversationId}/rerun`, {
    body: { sql, timezone: timezone(), source_message_id: sourceMessageId ?? undefined },
  })

export const submitFeedback = (body: FeedbackInput) => api<FeedbackView>('POST', '/api/feedback', { body })

/** 自己对这些回答的评价，刷新后按钮仍显示已选状态。 */
export const useMyFeedback = (queryRunIds: number[]) =>
  useQuery({
    queryKey: ['feedback', 'mine', queryRunIds],
    queryFn: () => api<MyFeedback[]>('GET', '/api/feedback/mine', { query: { query_run_ids: queryRunIds } }),
    enabled: queryRunIds.length > 0,
    staleTime: 60_000,
  })

/* ---- datasource ---- */
export const useDatasources = () =>
  useQuery({ queryKey: ['datasources'], queryFn: () => api<Page<DatasourceRead>>('GET', '/api/datasources', { query: { page_size: 100 } }) })

export function useSaveDatasource() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: DatasourceWrite }) =>
      id ? api<DatasourceRead>('PUT', `/api/datasources/${id}`, { body }) : api<DatasourceRead>('POST', '/api/datasources', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['datasources'] }),
  })
}

export const testDatasource = (id: number) => api<ConnectionTest>('POST', `/api/datasources/${id}/test`)

export function useSetDatasourceStatus() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api<DatasourceRead>('PATCH', `/api/datasources/${id}/status`, { body: { status } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['datasources'] }),
  })
}

export const useTables = (datasourceId: number | null) =>
  useQuery({ queryKey: ['tables', datasourceId], queryFn: () => api<TableRead[]>('GET', `/api/schema/${datasourceId}/tables`), enabled: datasourceId != null })

/** 当前用户在这个数据源上看得到的表和字段。 */
export const useScope = (datasourceId: number | null) =>
  useQuery({ queryKey: ['scope', datasourceId], queryFn: () => api<PublicScope>('GET', `/api/permissions/${datasourceId}/scope`), enabled: datasourceId != null, staleTime: 60 * 1000 })

/* ---- semantic / metric ---- */
export const useSemantics = (datasourceId: number | null) =>
  useQuery({ queryKey: ['semantic', datasourceId], queryFn: () => api<SemanticView[]>('GET', '/api/semantic', { query: { datasource_id: datasourceId } }), enabled: datasourceId != null })

export function useSaveSemantic(datasourceId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: SemanticInput }) =>
      id ? api<SemanticView>('PUT', `/api/semantic/${id}`, { body }) : api<SemanticView>('POST', '/api/semantic', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['semantic', datasourceId] }),
  })
}

export function useDeleteSemantic(datasourceId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<boolean>('DELETE', `/api/semantic/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['semantic', datasourceId] }),
  })
}

export const useMetrics = (datasourceId: number | null) =>
  useQuery({ queryKey: ['metric', datasourceId], queryFn: () => api<MetricView[]>('GET', '/api/metric', { query: { datasource_id: datasourceId } }), enabled: datasourceId != null })

export function useSaveMetric(datasourceId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: MetricInput }) =>
      id ? api<MetricView>('PUT', `/api/metric/${id}`, { body }) : api<MetricView>('POST', '/api/metric', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['metric', datasourceId] }),
  })
}

export function useDeleteMetric(datasourceId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<boolean>('DELETE', `/api/metric/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['metric', datasourceId] }),
  })
}

export const usePromotions = () => useQuery({ queryKey: ['promotion'], queryFn: () => api<PromotionView[]>('GET', '/api/promotion') })

export function usePromote() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { kind: 'semantic' | 'metric'; ref_id: number; comment?: string }) => api<PromotionView>('POST', '/api/promotion', { body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['promotion'] })
      void qc.invalidateQueries({ queryKey: ['semantic'] })
      void qc.invalidateQueries({ queryKey: ['metric'] })
    },
  })
}

export function useReviewPromotion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, approve, comment }: { id: number; approve: boolean; comment?: string }) =>
      api<PromotionView>('POST', `/api/promotion/${id}/review`, { body: { approve, comment: comment ?? '' } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['promotion'] })
      void qc.invalidateQueries({ queryKey: ['semantic'] })
      void qc.invalidateQueries({ queryKey: ['metric'] })
    },
  })
}

/* ---- query log / feedback / eval ---- */
export interface LogFilter {
  status?: string
  has_feedback?: boolean
  user_id?: number
  /** ISO 时间，含；until 不含。 */
  since?: string
  until?: string
  page: number
}

/** 可以审阅其查询与反馈的人：管理员看全部，负责人看本团队。 */
export const usePeople = () =>
  useQuery({ queryKey: ['people'], queryFn: () => api<PersonRead[]>('GET', '/api/org/people'), staleTime: 60_000 })

export const fetchQuery = (id: number) => api<QueryView>('GET', `/api/querylog/${id}`)

export const useQueryLog = (f: LogFilter) =>
  useQuery({
    queryKey: ['querylog', f],
    queryFn: () => api<Page<QueryView>>('GET', '/api/querylog', { query: { status: f.status, has_feedback: f.has_feedback, user_id: f.user_id, since: f.since, until: f.until, page: f.page, page_size: 20 } }),
    placeholderData: keepPreviousData,
  })

export const useFeedback = (f: LogFilter) =>
  useQuery({
    queryKey: ['feedback', f],
    queryFn: () => api<Page<FeedbackView>>('GET', '/api/feedback', { query: { status: f.status || undefined, user_id: f.user_id, since: f.since, until: f.until, page: f.page, page_size: 20 } }),
    placeholderData: keepPreviousData,
  })

export function useHandleFeedback() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<FeedbackView>('POST', `/api/feedback/${id}/handle`, { body: {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['feedback'] }),
  })
}

export const useEvalRun = (id: number | null) =>
  useQuery({
    queryKey: ['eval', 'run', id],
    queryFn: () => api<RunView>('GET', `/api/eval/runs/${id}`),
    enabled: id != null,
    refetchInterval: (q) => (q.state.data && ['queued', 'running'].includes(q.state.data.status) ? 3000 : false),
  })

/** 运行中每 3 秒刷新一次，逐题看到结果。 */
export const useEvalResults = (id: number | null, live = false) =>
  useQuery({ queryKey: ['eval', 'results', id], queryFn: () => api<Evaluation[]>('GET', `/api/eval/runs/${id}/results`), enabled: id != null, refetchInterval: live ? 3000 : false })

export const useEvalCases = () => useQuery({ queryKey: ['eval', 'cases'], queryFn: () => api<EvalCaseView[]>('GET', '/api/eval/cases') })

/** 有未结束的运行时每 3 秒刷新，评测记录里的状态跟着变。 */
export const useEvalRuns = () =>
  useQuery({
    queryKey: ['eval', 'runs'],
    queryFn: () => api<RunListItem[]>('GET', '/api/eval/runs'),
    refetchInterval: (q) => (q.state.data?.some((r) => ['queued', 'running'].includes(r.status)) ? 3000 : false),
  })

/* ---- org ---- */
import type { AuditView, ModelChoice, GrantRead, TeamCreate, VisibilityRead, FullVisibilityWrite, ModelRead, ModelWrite, PromptDetail, PromptSummary, QuotaUsage, QuotaView, RevisionRead, RevisionWrite, TrendPoint, TaskRecord, TeamRead, TeamWrite, UsageView, UserRead, UserUpdate, UserWrite } from './types'

/** 团队列表只有管理员能读；其他角色的页面传 enabled=false。 */
export const useTeams = (enabled = true) =>
  useQuery({ queryKey: ['teams'], queryFn: () => api<Page<TeamRead>>('GET', '/api/org/teams', { query: { page_size: 100 } }), enabled })

export function useSaveTeam() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: TeamWrite | TeamCreate }) =>
      id ? api<TeamRead>('PUT', `/api/org/teams/${id}`, { body }) : api<TeamRead>('POST', '/api/org/teams', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['teams'] }),
  })
}

export const useUsers = (page: number) =>
  useQuery({ queryKey: ['users', page], queryFn: () => api<Page<UserRead>>('GET', '/api/org/users', { query: { page, page_size: 20 } }), placeholderData: keepPreviousData })

/** 自由员工：尚未分配团队、可被拉进团队或指定为新负责人。 */
export const useFreeUsers = (enabled = true) =>
  useQuery({ queryKey: ['users', 'free'], queryFn: () => api<Page<UserRead>>('GET', '/api/org/users', { query: { free: true, page_size: 100 } }), enabled })

export const useTeamUsers = (teamId: number | null) =>
  useQuery({ queryKey: ['users', 'team', teamId], queryFn: () => api<Page<UserRead>>('GET', '/api/org/users', { query: { team_id: teamId, page_size: 100 } }), enabled: teamId != null })

/** 团队成员变动：更换负责人、拉入自由员工、移出团队，完成后刷新团队与成员列表。 */
export function useTeamMembers(teamId: number) {
  const qc = useQueryClient()
  const done = () => Promise.all([qc.invalidateQueries({ queryKey: ['teams'] }), qc.invalidateQueries({ queryKey: ['users'] })])
  return {
    changeLeader: useMutation({ mutationFn: (userId: number) => api<TeamRead>('PUT', `/api/org/teams/${teamId}/leader`, { body: { user_id: userId } }), onSuccess: done }),
    add: useMutation({ mutationFn: (userIds: number[]) => api<TeamRead>('POST', `/api/org/teams/${teamId}/members`, { body: { user_ids: userIds } }), onSuccess: done }),
    remove: useMutation({ mutationFn: (userId: number) => api<TeamRead>('DELETE', `/api/org/teams/${teamId}/members/${userId}`), onSuccess: done }),
  }
}

export function useSaveUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: UserWrite | UserUpdate }) =>
      id ? api<UserRead>('PUT', `/api/org/users/${id}`, { body }) : api<UserRead>('POST', '/api/org/users', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })
}

/* ---- schema ---- */
export const syncSchema = (datasourceId: number) => api<TaskRecord>('POST', `/api/schema/${datasourceId}/sync`)

export const useTask = (taskId: string | null) =>
  useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api<TaskRecord>('GET', `/api/schema/tasks/${taskId}`),
    enabled: taskId != null,
    refetchInterval: (q) => (q.state.data && ['queued', 'running'].includes(q.state.data.status) ? 1500 : false),
  })

export function useDescribe(kind: 'tables' | 'columns', datasourceId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, description }: { id: number; description: string }) => api<unknown>('PATCH', `/api/schema/${kind}/${id}`, { body: { description } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tables', datasourceId] }),
  })
}

/* ---- permissions ---- */
export const useGrants = (datasourceId: number | null, teamId: number | null) =>
  useQuery({
    queryKey: ['grants', datasourceId, teamId],
    queryFn: () => api<GrantRead>('GET', `/api/permissions/${datasourceId}/teams/${teamId}`),
    enabled: datasourceId != null && teamId != null,
  })

export function useSaveGrants(datasourceId: number | null, teamId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { mode: 'team' | 'members'; tables: unknown[] }) =>
      api<GrantRead>('PUT', `/api/permissions/${datasourceId}/teams/${teamId}${v.mode === 'members' ? '/members' : ''}`, { body: { tables: v.tables } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['grants', datasourceId, teamId] }),
  })
}

/** 提问时可选的已启用 LLM：任何登录用户都可读，只含名称，不含地址和密钥。 */
export const useModelChoices = () =>
  useQuery({ queryKey: ['model-choices'], queryFn: () => api<ModelChoice[]>('GET', '/api/models/choices'), staleTime: 60_000 })

/** 管理员的层级可见范围：数据源 → 表 → 字段，未自定义的向上继承。 */
export const useVisibility = (datasourceId: number | null) =>
  useQuery({ queryKey: ['visibility', datasourceId], queryFn: () => api<VisibilityRead>('GET', `/api/permissions/${datasourceId}/visibility`), enabled: datasourceId != null })

export function useSaveVisibility(datasourceId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: FullVisibilityWrite) => api<VisibilityRead>('PUT', `/api/permissions/${datasourceId}/visibility/all`, { body }),
    onSuccess: (data) => {
      qc.setQueryData(['visibility', datasourceId], data)
      return qc.invalidateQueries({ queryKey: ['grants', datasourceId] })
    },
  })
}

/* ---- models ---- */
export const useModels = () =>
  useQuery({ queryKey: ['models'], queryFn: () => api<Page<ModelRead>>('GET', '/api/models', { query: { page_size: 100 } }) })

/** 只切换启用状态，不需要重新填写密钥。 */
export function useSetModelEnabled() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => api<ModelRead>('PATCH', `/api/models/${id}/enabled`, { body: { enabled } }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ['models'] }), qc.invalidateQueries({ queryKey: ['model-choices'] })]),
  })
}

export function useSaveModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: ModelWrite }) =>
      id ? api<ModelRead>('PUT', `/api/models/${id}`, { body }) : api<ModelRead>('POST', '/api/models', { body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ['models'] }), qc.invalidateQueries({ queryKey: ['model-choices'] })]),
  })
}

export const testModel = (id: number) => api<{ available: boolean }>('POST', `/api/models/${id}/test`)

export const usePreference = (teamId: number | null, kind: 'llm' | 'embedding' | 'rerank') =>
  useQuery({
    queryKey: ['preference', teamId, kind],
    queryFn: () => api<{ model_id: number }>('GET', `/api/models/teams/${teamId}/${kind}`),
    enabled: teamId != null,
    retry: false,
  })

export function useSetPreference(teamId: number | null, kind: 'llm' | 'embedding' | 'rerank') {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (modelId: number) => api<{ model_id: number }>('PUT', `/api/models/teams/${teamId}/${kind}`, { body: { model_id: modelId } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['preference', teamId, kind] }),
  })
}

/* ---- usage / audit ---- */
export const useUsage = (page: number) =>
  useQuery({ queryKey: ['usage', page], queryFn: () => api<Page<UsageView>>('GET', '/api/usage', { query: { page, page_size: 20 } }), placeholderData: keepPreviousData })

export const useUsageTrend = (by: 'team' | 'model', days: number) =>
  useQuery({
    queryKey: ['usage', 'trend', by, days],
    queryFn: () => {
      const until = new Date()
      const since = new Date(until.getTime() - (days - 1) * 86_400_000)
      const day = (d: Date) => d.toISOString().slice(0, 10)
      return api<TrendPoint[]>('GET', '/api/usage/trend', { query: { by, since: day(since), until: day(until) } }).then((points) => ({ points, since: day(since), until: day(until) }))
    },
  })

export const useQuotaAlerts = () => useQuery({ queryKey: ['usage', 'alerts'], queryFn: () => api<QuotaUsage[]>('GET', '/api/usage/alerts'), refetchInterval: 60_000 })

export const useQuota = (teamId: number | null) =>
  useQuery({ queryKey: ['quota', teamId], queryFn: () => api<QuotaView>('GET', `/api/usage/quota/${teamId}`), enabled: teamId != null })

export function useSaveQuota(teamId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { daily_query_limit: number | null; monthly_token_limit: number | null }) => api<QuotaView>('PUT', `/api/usage/quota/${teamId}`, { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['quota', teamId] }),
  })
}

export const useAudit = (page: number, action: string) =>
  useQuery({
    queryKey: ['audit', page, action],
    queryFn: () => api<Page<AuditView>>('GET', '/api/audit', { query: { page, page_size: 20, action: action || undefined } }),
    placeholderData: keepPreviousData,
  })

/* ---- eval ---- */
export interface RunComparison {
  before_id: number
  after_id: number
  new_failures: string[]
  fixed: string[]
  added: string[]
  removed: string[]
}

export const loadEvalCases = () => api<number>('POST', '/api/eval/cases/load')
export const startEval = (body: { model_id: string; case_ids: string[]; note?: string }) => api<RunView>('POST', '/api/eval/runs', { body })
export const getEvalRun = (id: number) => api<RunView>('GET', `/api/eval/runs/${id}`)
export const resumeEval = (id: number) => api<RunView>('POST', `/api/eval/runs/${id}/resume`)
export const cancelEval = (id: number) => api<RunView>('POST', `/api/eval/runs/${id}/cancel`)
export const deleteEval = (id: number) => api<null>('DELETE', `/api/eval/runs/${id}`)
export const useEvalCompare = (before: number | null, after: number | null) =>
  useQuery({
    queryKey: ['eval', 'compare', before, after],
    queryFn: () => api<RunComparison>('GET', '/api/eval/runs/compare', { query: { before, after } }),
    enabled: before != null && after != null && before !== after,
  })

/* ---- suggestions / context preview ---- */

export const useSuggestedQuestions = (datasourceId: number | null) =>
  useQuery({
    queryKey: ['suggested', datasourceId],
    queryFn: () => api<SuggestedQuestions>('GET', '/api/chat/suggested-questions', { query: { datasource_id: datasourceId } }),
    enabled: datasourceId != null,
    staleTime: 10 * 60 * 1000,
  })

export interface ContextPreview {
  mode: 'full' | 'hybrid'
  tables: Record<string, Record<string, string>>
  items: { kind: string; name: string; scope: 'platform' | 'team' | 'personal'; version: number; definition: string; pinned: boolean }[]
  required_conditions: string[]
  ambiguities: { question: string; choices: string[] }[]
}

export const useContextPreview = (body: { datasource_id: number | null; question: string; referenced_metric_ids: number[] }, enabled: boolean) =>
  useQuery({
    queryKey: ['context-preview', body],
    queryFn: () => api<ContextPreview>('POST', '/api/chat/context-preview', { body }),
    enabled: enabled && body.datasource_id != null,
  })

export const testDatasourceDraft = (body: DatasourceWrite) => api<ConnectionTest>('POST', '/api/datasources/test', { body })
export const testModelDraft = (body: ModelWrite) => api<{ available: boolean }>('POST', '/api/models/test', { body })

/** 触发元数据同步并等到任务结束；返回最终状态和错误码。 */
export async function runSync(datasourceId: number): Promise<{ status: string; error: string | null }> {
  const task = await syncSchema(datasourceId)
  for (let i = 0; i < 120; i++) {
    const t = await api<TaskRecord>('GET', `/api/schema/tasks/${task.id}`)
    if (t.status === 'success' || t.status === 'failed') return { status: t.status, error: t.error ?? null }
    await new Promise((r) => setTimeout(r, 1000))
  }
  return { status: 'timeout', error: null }
}

/* ---- prompts ---- */
export const usePrompts = () => useQuery({ queryKey: ['prompts'], queryFn: () => api<PromptSummary[]>('GET', '/api/prompts') })

export const usePromptDetail = (kind: string, key: string) =>
  useQuery({ queryKey: ['prompts', kind, key], queryFn: () => api<PromptDetail>('GET', `/api/prompts/${kind}/${key}`) })

export const fetchRevision = (kind: string, key: string, id: number) => api<RevisionRead>('GET', `/api/prompts/${kind}/${key}/revisions/${id}`)

export function usePromptActions(kind: string, key: string) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: ['prompts'] })
  return {
    publish: useMutation({ mutationFn: (body: RevisionWrite) => api<RevisionRead>('POST', `/api/prompts/${kind}/${key}/revisions`, { body }), onSuccess: refresh }),
    activate: useMutation({ mutationFn: (id: number) => api<null>('POST', `/api/prompts/${kind}/${key}/revisions/${id}/activate`), onSuccess: refresh }),
    reset: useMutation({ mutationFn: () => api<null>('POST', `/api/prompts/${kind}/${key}/reset`), onSuccess: refresh }),
  }
}

/* ---- Excel / CSV 上传 ---- */
export const previewUpload = (body: { filename: string; content_base64: string; datasource_id?: number; datasource_name: string; config: Partial<UploadParseConfig> }) =>
  api<UploadPreview>('POST', '/api/upload/preview', { body })
/** 换工作表或表头行：服务端复制已上传的文件重新预览，不必再传一次。 */
export const repreviewUpload = (id: number, config: Partial<UploadParseConfig>) => api<UploadPreview>('POST', `/api/upload/${id}/sheets`, { body: config })
export const confirmUpload = (id: number, config: UploadParseConfig, mode: 'overwrite' | 'append') =>
  api<UploadJob>('POST', `/api/upload/${id}/confirm`, { body: { config, mode } })
export const getUpload = (id: number) => api<UploadJob>('GET', `/api/upload/${id}`)
export const publishUpload = (id: number) => api<UploadJob>('POST', `/api/upload/${id}/publish`)
