import type { components } from './schema'

type S = components['schemas']

export type ErrorCode = S['ErrorCode']
export type Role = S['Role']
export type CurrentUser = S['CurrentUser']
export type TokenPair = S['TokenPair']
export type ConversationView = S['ConversationView']
export type MessageView = S['MessageView']
export type QueryReply = S['QueryReply']
export type ChartSpec = S['ChartSpec']
export type Clarification = S['Clarification']
export type AnswerMeta = S['AnswerMeta']
export type UsedDefinition = S['UsedDefinition']
export type ChatLimits = S['ChatLimits']
export type QueryView = S['QueryView']
export type FeedbackView = S['FeedbackView']
export type FeedbackInput = S['FeedbackInput']
export type MyFeedback = S['MyFeedback']
export type DatasourceRead = S['DatasourceRead']
export type DatasourceChoice = S['DatasourceChoice']
export type DatasourceWrite = S['DatasourceWrite']
export type ConnectionTest = S['ConnectionTest']
export type SemanticView = S['SemanticView']
export type SemanticInput = S['SemanticInput']
export type MetricView = S['MetricView']
export type MetricInput = S['MetricInput']
export type PromotionView = S['PromotionView']
export type RunView = S['RunView']
export type Evaluation = S['EvalResultView']
export type EvalCaseView = S['EvalCaseView']
export type RunListItem = S['RunListItem']
export type TableRead = S['TableRead']
export type ScopeLevel = S['ScopeLevel']

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/** SSE event payloads emitted by the query pipeline (core/stream EventType). */
export type StreamEvent =
  | { event: 'stage'; data: { name: string } }
  | { event: 'sql'; data: { sql: string } }
  | { event: 'data'; data: { columns: string[]; rows: Record<string, unknown>[]; truncated: boolean; row_count?: number; preview_truncated?: boolean } }
  | { event: 'chart'; data: ChartSpec }
  | { event: 'explanation'; data: { meta?: AnswerMeta; clarification?: Clarification } }
  | { event: 'reasoning'; data: { delta: string } }
  | { event: 'answer'; data: { delta: string } }
  | { event: 'title'; data: { title: string } }
  | { event: 'error'; data: { code: ErrorCode; message: string } }
  | { event: 'done'; data: { status: QueryReply['status']; query_run_id?: number | null; message_id?: number | null } }

export type TeamRead = S['TeamRead']
export type TeamWrite = S['TeamWrite']
export type UserRead = S['UserRead']
export type UserWrite = S['UserWrite']
export type UserUpdate = S['UserUpdate']
export type ColumnRead = S['ColumnRead']
export type PublicScope = S['PublicScope']
export type TaskRecord = S['TaskRecord']
export type GrantRead = S['GrantRead']
export type TableGrantWrite = S['TableGrantWrite']
export type ColumnGrantWrite = S['ColumnGrantWrite']
export type MaskRule = S['MaskRule']
export type ModelRead = S['ModelRead']
export type ModelWrite = S['ModelWrite']
export type UsageView = S['UsageView']
export type QuotaView = S['QuotaView']
export type PromptSummary = S['PromptSummary']
export type PromptDetail = S['PromptDetail']
export type RevisionMeta = S['RevisionMeta']
export type RevisionRead = S['RevisionRead']
export type RevisionWrite = S['RevisionWrite']
export type TrendPoint = S['TrendPoint']
export type QuotaUsage = S['QuotaUsage']
export type AuditView = S['AuditView']
export type Preview = S['Preview']
export type ParseConfig = S['ParseConfig']
export type ImportColumn = S['ImportColumn']
export type McpClientRead = S['McpClientRead']
export type McpClientWrite = S['McpClientWrite']
export type McpKeyRead = S['McpKeyRead']
export type McpKeyCreated = S['McpKeyCreated']
export type ClientGrantsRead = S['ClientGrantsRead']
export type ClientColumnWrite = S['ClientColumnWrite']
export type OverlayItem = S['OverlayItem']
export type OverrideWrite = S['OverrideWrite']
export type TestAskReply = S['TestAskReply']
export type TestDiagnostics = S['TestDiagnostics']
export type JobView = S['JobView']
export type TeamCreate = S['TeamCreate']
export type VisibilityRead = S['VisibilityRead']
export type TableVisibility = S['TableVisibility']
export type ColumnVisibility = S['ColumnVisibility']
export type ModelChoice = S['ModelChoice']
export type FullVisibilityWrite = S['FullVisibilityWrite']
export type UploadPreview = S['Preview']
export type UploadParseConfig = S['ParseConfig']
export type UploadColumn = S['ImportColumn']
export type UploadJob = S['JobView']
export type PersonRead = S['PersonRead']
export type SuggestedQuestion = S['SuggestedQuestion']
export type SuggestedQuestions = S['SuggestedQuestions']
export type SetupRead = S['SetupRead']
export type SetupWrite = S['SetupWrite']
export type SetupResult = S['SetupResult']
export type SetupSummary = S['SetupSummary']
export type DraftResult = S['DraftResult']
