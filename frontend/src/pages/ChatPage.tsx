import { Icon } from '../components/Icon'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { streamQuestion } from '../api/client'
import { rerunSql, submitFeedback, timezone, useAvailableDatasources, useChatLimits, useConversation, useConversations, useCreateConversation, useDeleteConversation, useMessages, useMyFeedback, useRenameConversation, useReplay, useMetrics, useModelChoices, useSuggestedQuestions } from '../api/hooks'
import type { ChatLimits, ConversationView, FeedbackInput, MessageView, MyFeedback } from '../api/types'
import { Button, Callout, Panel, TextField } from '../components/basic'
import { ChatMessage, ClarifyPrompt, Composer, EmptyResultHint, ErrorNotice, FeedbackBar, SqlBlock, SuggestedQuestions, type FeedbackRating } from '../components/chat'
import { ReasoningPanel } from '../components/reasoning'
import { ResultCard } from '../components/result'
import { useToast } from '../components/Toast'
import { extractWhereClause } from '../lib/sql'
import { Dialog, DialogFooter } from '../components/Dialog'
import { DEFAULT_LAYOUT, Resizer, loadLayout, saveLayout, type Layout } from '../components/Resizer'
import { ApiError, codeFromMessage, describeError } from '../lib/errors'
import { formatMs, formatTime } from '../lib/format'
import { applyEvent, conclusion, legacyAnswer, newTurn, STAGE_LABEL, turnFromReply, type Turn } from './turns'
import { download } from '../api/client'
import { Select } from '../components/Select'

const MODEL_KEY = 'askdata.model'
const MANUAL_RERUN = '手动修改 SQL 后重新执行'

export function ChatPage(props: { conversationId: number | null; onSelect: (id: number | null) => void }) {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const query = useDebounced(search.trim(), 250)
  const conversations = useConversations(query)
  const list = useMemo(() => (conversations.data?.pages ?? []).flatMap((p) => p.items), [conversations.data])
  const total = conversations.data?.pages[0]?.total ?? 0
  const currentQuery = useConversation(props.conversationId)
  const datasources = useAvailableDatasources()
  const create = useCreateConversation()
  const remove = useDeleteConversation()
  const [limit, setLimit] = useState(100)
  const history = useMessages(props.conversationId, limit)
  const messages = useMemo(() => history.data?.items ?? [], [history.data])
  const earlier = (history.data?.total ?? 0) - messages.length
  const [turns, setTurns] = useState<Turn[]>([])
  const [streaming, setStreaming] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [dialog, setDialog] = useState(false)
  const [deleting, setDeleting] = useState<ConversationView | null>(null)
  const [fill, setFill] = useState<{ text: string; nonce: number } | undefined>()
  const narrow = useNarrow()
  const [narrowOpen, setNarrowOpen] = useState(false)
  const modelChoices = useModelChoices()
  const [pickedModel, setPickedModel] = useState<number | null>(() => Number(localStorage.getItem(MODEL_KEY)) || null)
  const models = modelChoices.data ?? []
  const modelId = models.find((m) => m.id === pickedModel)?.id ?? models.find((m) => m.is_default)?.id ?? models[0]?.id ?? null
  const chooseModel = (id: number) => {
    setPickedModel(id)
    localStorage.setItem(MODEL_KEY, String(id))
  }
  const [layout, setLayout] = useState<Layout>(loadLayout)
  const updateLayout = (patch: Partial<Layout>) =>
    setLayout((cur) => {
      const next = { ...cur, ...patch }
      saveLayout(next)
      return next
    })
  // 窄屏默认收起会话列表，给对话区让出空间；展开只在本次有效，不改宽屏的偏好。
  const collapsed = narrow ? !narrowOpen : layout.leftCollapsed
  const setCollapsed = (value: boolean) => (narrow ? setNarrowOpen(!value) : updateLayout({ leftCollapsed: value }))
  const abort = useRef<AbortController | null>(null)

  const current = currentQuery.data ?? null
  const sources = datasources.data?.items ?? []
  const sourceName = (id: number) => {
    const d = sources.find((x) => x.id === id)
    return d ? `${d.type} · ${d.name}` : `数据源 ${id}`
  }
  const metrics = useMetrics(current?.datasource_id ?? null)
  const suggestions = useSuggestedQuestions(current?.datasource_id ?? null)
  const mentionable = (metrics.data ?? []).map((m) => ({ id: m.id, name: m.name, aliases: m.aliases ?? [] }))
  const limits = useChatLimits()
  const { container: convoRef, content: threadRef, onScroll, toBottom, keepPosition, away } = useStickToBottom()

  // 自己对这些回答的评价：刷新后按钮仍是已选状态，再点是修改而不是重复提交。
  const runIds = useMemo(
    () => [...new Set([...messages.map((m) => m.query_run_id), ...turns.map((t) => t.queryRunId)].filter((id): id is number => id != null))],
    [messages, turns],
  )
  const myFeedback = useMyFeedback(runIds)
  const feedbackOf = (id: number | null | undefined) => (id == null ? null : (myFeedback.data ?? []).find((f) => f.query_run_id === id) ?? null)
  const rate = async (queryRunId: number, rating: FeedbackRating, reason?: FeedbackInput['reason_code'], comment?: string) => {
    await submitFeedback({ query_run_id: queryRunId, rating, reason_code: reason ?? null, comment: comment ?? '' })
    void qc.invalidateQueries({ queryKey: ['feedback', 'mine'] })
  }

  // The route keys this component by conversation, so state starts clean; only cancel in-flight work.
  useEffect(() => () => abort.current?.abort(), [])

  const patch = useCallback((key: string, fn: (t: Turn) => Turn) => setTurns((all) => all.map((t) => (t.key === key ? fn(t) : t))), [])

  const ask = useCallback(
    async (question: string, clarify?: { messageId: number; choice: string }, metricIds: number[] = []) => {
      if (props.conversationId == null) return
      const turn = newTurn(question)
      setTurns((all) => [...all, turn])
      setNotice(null)
      setStreaming(true)
      toBottom()
      const controller = new AbortController()
      abort.current = controller
      try {
        await streamQuestion(
          props.conversationId,
          { question, timezone: timezone(), clarification_message_id: clarify?.messageId, clarification_choice: clarify?.choice, referenced_metric_ids: metricIds.length ? metricIds : undefined, model_id: modelId ?? undefined },
          (ev) => {
            patch(turn.key, (t) => applyEvent(t, ev))
            // 第一问生成了会话标题：刷新左侧列表。
            if (ev.event === 'title') void qc.invalidateQueries({ queryKey: ['conversations'] })
          },
          controller.signal,
        )
      } catch (e) {
        if (controller.signal.aborted) {
          patch(turn.key, (t) => ({ ...t, status: 'failed', errorCode: 'QUERY_CANCELLED', ms: Date.now() - t.startedAt }))
        } else {
          const code = e instanceof ApiError ? e.code : 'INTERNAL_ERROR'
          patch(turn.key, (t) => ({ ...t, status: 'failed', errorCode: code as Turn['errorCode'], ms: Date.now() - t.startedAt }))
          setNotice(describeError(code))
        }
      } finally {
        setStreaming(false)
        // 提问是一次活动：会话移到列表最前。
        void qc.invalidateQueries({ queryKey: ['conversations'] })
      }
    },
    [patch, props.conversationId, qc, modelId, toBottom],
  )

  /** 失败或停止的提问：原样再问一次，或放回输入框改一改再问。 */
  const retry: RetryActions = {
    again: (question) => void ask(question),
    edit: (question) => setFill({ text: question, nonce: Date.now() }),
    disabled: streaming,
  }

  const pick = (turn: Turn, label: string) => {
    if (turn.clarifyMessageId == null) return
    patch(turn.key, (t) => ({ ...t, picked: label }))
    void ask(label, { messageId: turn.clarifyMessageId, choice: label })
  }

  const rerun = async (sql: string, sourceMessageId?: number | null) => {
    if (props.conversationId == null) return
    setStreaming(true)
    toBottom()
    const started = Date.now()
    try {
      const reply = await rerunSql(props.conversationId, sql, sourceMessageId)
      const turn = { ...turnFromReply(MANUAL_RERUN, reply), ms: Date.now() - started }
      setTurns((all) => [...all, turn])
    } catch (e) {
      setNotice(describeError(e instanceof ApiError ? e.code : undefined))
    } finally {
      setStreaming(false)
      void qc.invalidateQueries({ queryKey: ['conversations'] })
    }
  }

  const createConversation = async (datasourceId: number) => {
    const c = await create.mutateAsync({ datasource_id: datasourceId })
    setDialog(false)
    setSearch('')
    props.onSelect(c.id)
  }
  // 只有一个数据源时不用再选，直接建；多个时默认选当前会话的数据源。
  const startNew = () => (sources.length === 1 && sources[0] ? void createConversation(sources[0].id) : setDialog(true))

  // 只自动读取最近 3 条带结果的回答，更早的由用户点开，避免打开长会话时一次执行很多查询。
  const recent = useMemo(() => new Set(messages.filter((m) => m.role === 'assistant' && m.normalized_sql).slice(-3).map((m) => m.id)), [messages])
  const lastMessageId = messages[messages.length - 1]?.id
  const isEmpty = turns.length === 0 && messages.length === 0 && !history.isPending

  return (
    <div className="cb-work-chat">
      {collapsed ? null : (
        <>
      <Panel className="cb-side-panel" style={{ width: layout.left }} title="会话" actions={
        <div className="cb-row">
          <Button size="sm" variant="ghost" icon="plus" aria-label="新会话" title="新会话" disabled={!sources.length || create.isPending} onClick={startNew} />
          <button type="button" className="cb-icon-btn" aria-label="折叠会话列表" title="折叠会话列表" onClick={() => setCollapsed(true)}>
            <Icon name="sidebarCollapse" />
          </button>
        </div>
      }>
        {total > 8 || search ? (
          <TextField aria-label="搜索会话" placeholder="搜索会话标题" icon="search" value={search} onChange={(e) => setSearch(e.target.value)} />
        ) : null}
        <ul className="cb-sessions">
          {list.map((c) => (
            <SessionItem
              key={c.id}
              conversation={c}
              current={c.id === props.conversationId}
              source={sources.length > 1 ? sources.find((d) => d.id === c.datasource_id)?.name : undefined}
              onSelect={() => props.onSelect(c.id)}
              onDelete={() => setDeleting(c)}
            />
          ))}
        </ul>
        {conversations.hasNextPage ? (
          <Button size="sm" variant="ghost" disabled={conversations.isFetchingNextPage} onClick={() => void conversations.fetchNextPage()}>
            {conversations.isFetchingNextPage ? '加载中…' : `加载更多（还有 ${total - list.length} 个）`}
          </Button>
        ) : null}
        {conversations.data && list.length === 0 ? <div className="cb-muted">{query ? `没有标题包含「${query}」的会话` : '还没有会话'}</div> : null}
      </Panel>
      <Resizer label="调整会话列表宽度" side="left" value={layout.left} min={160} max={420} defaultValue={DEFAULT_LAYOUT.left} onChange={(left) => updateLayout({ left })} />
        </>
      )}

      <section className="cb-panel cb-chat-col">
        {current ? (
          <>
            {isEmpty ? (
              <div className="cb-hero">
                <div className="display">问一个数据问题</div>
                <p>当前数据源：{sourceName(current.datasource_id)}。用自然语言提问，输入 @ 可以指定指标。</p>
                <SuggestedQuestions questions={suggestions.data?.questions ?? []} disabled={streaming} onPick={(q) => void ask(q)} />
              </div>
            ) : (
              <div className="cb-convo" style={{ flex: 1 }} ref={convoRef} onScroll={onScroll}>
                <div className="cb-thread" ref={threadRef}>
                  {earlier > 0 ? (
                    <div className="cb-earlier">
                      <Button size="sm" variant="ghost" disabled={history.isFetching} onClick={() => { keepPosition(); setLimit((n) => n + 100) }}>
                        {history.isFetching ? '加载中…' : `显示更早的 ${earlier} 条消息`}
                      </Button>
                    </div>
                  ) : null}
                  {messages.map((m, i, all) => (
                    <HistoryMessage
                      key={m.id}
                      message={m}
                      question={all[i - 1]?.role === 'user' ? all[i - 1]?.content : undefined}
                      conversationId={current.id}
                      auto={recent.has(m.id)}
                      onRun={(sql, source) => void rerun(sql, source)}
                      running={streaming}
                      limits={limits.data}
                      stopped={m.role === 'user' && (all[i + 1]?.role === 'user' || i === all.length - 1)}
                      retry={retry}
                      // 只有最后一条、还没回答的澄清可以继续选；后面已经有新提问的就不再接着答。
                      onPick={m.id === lastMessageId && turns.length === 0 && !m.clarification_answered ? (label) => void ask(label, { messageId: m.id, choice: label }) : undefined}
                      feedback={feedbackOf(m.query_run_id)}
                      onRate={rate}
                    />
                  ))}
                  {turns.map((t) => (
                    <TurnView
                      key={t.key}
                      turn={t}
                      conversationId={current.id}
                      onRun={(sql, source) => void rerun(sql, source)}
                      running={streaming}
                      onPick={(label) => pick(t, label)}
                      limits={limits.data}
                      retry={retry}
                      feedback={feedbackOf(t.queryRunId)}
                      onRate={rate}
                    />
                  ))}
                  {notice ? <Callout tone="danger" title="请求失败">{notice}</Callout> : null}
                </div>
              </div>
            )}
            {away && !isEmpty ? (
              <button type="button" className="cb-jump" onClick={toBottom}>
                <Icon name="chevronDown" size="sm" />
                回到最新
              </button>
            ) : null}
            <div className="cb-chat-compose">
              <Composer
                source={sourceName(current.datasource_id)}
                streaming={streaming}
                metrics={mentionable}
                models={models}
                modelId={modelId}
                onModelChange={chooseModel}
                onSubmit={(q, ids) => void ask(q, undefined, ids)}
                onStop={() => abort.current?.abort()}
                draftKey={String(current.id)}
                fill={fill}
              />
            </div>
          </>
        ) : props.conversationId != null && currentQuery.isError ? (
          <div className="cb-hero">
            <div className="display">会话不存在</div>
            <p>这个会话可能已被删除，或者不属于你。</p>
            <Button variant="primary" onClick={() => props.onSelect(null)}>
              返回
            </Button>
          </div>
        ) : props.conversationId != null ? (
          <div className="cb-hero">
            <div className="cb-muted">正在打开会话…</div>
          </div>
        ) : (
          <EmptyState
            hasDatasource={sources.length > 0}
            recent={search ? [] : list.slice(0, 3)}
            onOpen={props.onSelect}
            busy={create.isPending}
            onCreate={startNew}
          />
        )}
      </section>

      {collapsed ? (
        <button type="button" className="cb-float-toggle cb-float-left" aria-label="展开会话列表" title="展开会话列表" onClick={() => setCollapsed(false)}>
          <Icon name="sidebarExpand" />
        </button>
      ) : null}

      {deleting ? (
        <Dialog title="删除会话" onClose={() => setDeleting(null)}>
          <p style={{ margin: 0 }}>删除「{deleting.title}」后无法恢复，确定删除吗？</p>
          {remove.isError ? <Callout tone="danger">删除失败，请稍后重试。</Callout> : null}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              取消
            </Button>
            <Button
              variant="danger"
              disabled={remove.isPending}
              onClick={() => {
                const id = deleting.id
                remove.mutate(id, {
                  onSuccess: () => {
                    setDeleting(null)
                    localStorage.removeItem(`askdata.draft.${id}`)
                    if (id === props.conversationId) props.onSelect(null)
                  },
                })
              }}
            >
              删除
            </Button>
          </DialogFooter>
        </Dialog>
      ) : null}

      {dialog ? (
        <NewConversation
          choices={sources}
          initial={current?.datasource_id}
          busy={create.isPending}
          error={create.isError}
          onClose={() => setDialog(false)}
          onCreate={(id) => void createConversation(id)}
        />
      ) : null}
    </div>
  )
}

interface RetryActions {
  again: (question: string) => void
  edit: (question: string) => void
  disabled: boolean
}

function RetryBar(props: { question?: string; retry: RetryActions }) {
  if (!props.question) return null
  return (
    <div className="cb-row">
      <Button size="sm" variant="ghost" icon="refresh" disabled={props.retry.disabled} onClick={() => props.question && props.retry.again(props.question)}>
        重新提问
      </Button>
      <Button size="sm" variant="ghost" icon="edit" disabled={props.retry.disabled} onClick={() => props.question && props.retry.edit(props.question)}>
        编辑后提问
      </Button>
    </div>
  )
}

/** 会话列表的一项：标题悬停看全；改名就地编辑（回车保存、Esc 取消）。 */
function SessionItem(props: { conversation: ConversationView; current: boolean; source?: string; onSelect: () => void; onDelete: () => void }) {
  const c = props.conversation
  const rename = useRenameConversation()
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(c.title)
  const save = () => {
    const next = title.trim()
    setEditing(false)
    if (next && next !== c.title) rename.mutate({ id: c.id, title: next }, { onError: () => setTitle(c.title) })
    else setTitle(c.title)
  }
  return (
    <li aria-current={props.current} onClick={() => !editing && props.onSelect()}>
      <div>
        {editing ? (
          <input
            className="cb-session-rename"
            aria-label="会话名称"
            autoFocus
            maxLength={200}
            value={title}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={save}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) save()
              if (e.key === 'Escape') {
                setTitle(c.title)
                setEditing(false)
              }
            }}
          />
        ) : (
          <b title={c.title}>{rename.isPending ? title : c.title}</b>
        )}
        <span>
          {formatTime(c.updated_at)}
          {props.source ? ` · ${props.source}` : ''}
          {rename.isError ? ' · 改名失败' : ''}
        </span>
      </div>
      {editing ? null : (
        <div className="cb-session-actions">
          <Button
            size="sm"
            variant="ghost"
            icon="edit"
            aria-label={`重命名会话 ${c.title}`}
            title="重命名"
            onClick={(e) => {
              e.stopPropagation()
              setTitle(c.title)
              setEditing(true)
            }}
          />
          <Button
            size="sm"
            variant="ghost"
            icon="x"
            aria-label={`删除会话 ${c.title}`}
            title="删除"
            onClick={(e) => {
              e.stopPropagation()
              props.onDelete()
            }}
          />
        </div>
      )}
    </li>
  )
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return debounced
}

/**
 * 停在底部时内容变高（流式输出、历史结果读取完）就跟着滚到底；用户往上翻看时不打扰，
 * 并显示「回到最新」。加载更早的消息时保持当前阅读位置不跳。
 */
function useStickToBottom() {
  const box = useRef<HTMLDivElement | null>(null)
  const stick = useRef(true)
  const anchor = useRef<number | null>(null)
  const [away, setAway] = useState(false)
  const toBottom = useCallback(() => {
    stick.current = true
    setAway(false)
    const el = box.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])
  const lastTop = useRef(0)
  const onScroll = useCallback(() => {
    const el = box.current
    if (!el) return
    // 内容变高（结果读取完）也会触发滚动事件；只有向上滚才算用户离开了底部。
    if (el.scrollTop < lastTop.current - 2) stick.current = false
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) stick.current = true
    lastTop.current = el.scrollTop
    setAway(!stick.current)
  }, [])
  const keepPosition = useCallback(() => {
    const el = box.current
    if (el) anchor.current = el.scrollHeight - el.scrollTop
  }, [])
  const container = useCallback((node: HTMLDivElement | null) => {
    box.current = node
  }, [])
  const content = useCallback((node: HTMLDivElement | null) => {
    if (!node || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      const el = box.current
      if (!el) return
      if (anchor.current != null) {
        el.scrollTop = el.scrollHeight - anchor.current
        anchor.current = null
      } else if (stick.current) el.scrollTop = el.scrollHeight
    })
    observer.observe(node)
  }, [])
  return { container, content, onScroll, toBottom, keepPosition, away }
}

const NARROW = '(max-width: 1279px)'

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && Boolean(window.matchMedia?.(NARROW).matches))
  useEffect(() => {
    const query = window.matchMedia?.(NARROW)
    if (!query) return
    const update = () => setNarrow(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return narrow
}

function EmptyState(props: { hasDatasource: boolean; recent: ConversationView[]; onOpen: (id: number) => void; busy: boolean; onCreate: () => void }) {
  return (
    <div className="cb-hero">
      <div className="display">问一个数据问题</div>
      <p>
        {!props.hasDatasource
          ? '你还没有可用的数据源，请联系团队负责人开通权限。'
          : props.recent.length
            ? '接着最近的会话问，或者新建一个会话。'
            : '新建一个会话开始提问。数据源在会话创建时绑定，之后不可切换。'}
      </p>
      {props.recent.length ? (
        <div className="cb-row" role="group" aria-label="最近的会话" style={{ flexWrap: 'wrap', justifyContent: 'center' }}>
          {props.recent.map((c) => (
            <button key={c.id} type="button" className="cb-chip cb-recent-chip" title={c.title} onClick={() => props.onOpen(c.id)}>
              <span>{c.title}</span>
            </button>
          ))}
        </div>
      ) : null}
      {props.hasDatasource ? (
        <Button variant="primary" icon="plus" disabled={props.busy} onClick={props.onCreate}>
          新建会话
        </Button>
      ) : null}
    </div>
  )
}

function NewConversation(props: { choices: { id: number; name: string; type: string }[]; initial?: number; busy: boolean; error: boolean; onClose: () => void; onCreate: (id: number) => void }) {
  const [id, setId] = useState<number | null>(props.choices.find((c) => c.id === props.initial)?.id ?? props.choices[0]?.id ?? null)
  return (
    <Dialog title="新建会话" onClose={props.onClose}>
      <div className="cb-field">
        <label htmlFor="ds">数据源</label>
        <Select id="ds" value={id} onChange={setId} options={props.choices.map((c) => ({ value: c.id, label: `${c.type} · ${c.name}` }))} />
        <div className="cb-field-hint">创建后不可切换数据源。</div>
      </div>
      {props.error ? <Callout tone="danger">没能创建会话，请稍后重试。</Callout> : null}
      <DialogFooter>
        <Button variant="ghost" onClick={props.onClose}>
          取消
        </Button>
        <Button variant="primary" disabled={id == null || props.busy} onClick={() => id != null && props.onCreate(id)}>
          创建会话
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

interface Rating {
  feedback: MyFeedback | null
  onRate: (queryRunId: number, rating: FeedbackRating, reason?: FeedbackInput['reason_code'], comment?: string) => Promise<void>
}

function HistoryMessage(props: {
  message: MessageView
  /** 这条回答对应的提问，失败时用来重新提问。 */
  question?: string
  conversationId: number
  auto: boolean
  onRun: (sql: string, sourceMessageId?: number | null) => void
  running: boolean
  limits?: ChatLimits
  stopped?: boolean
  retry: RetryActions
  /** 仍可作答的澄清（会话最后一条、尚未回答）。 */
  onPick?: (label: string) => void
} & Rating) {
  const m = props.message
  const [open, setOpen] = useState(false)
  const hasResult = m.role === 'assistant' && Boolean(m.normalized_sql)
  const replay = useReplay(props.conversationId, m.id, hasResult && (props.auto || open))
  if (m.role === 'user')
    return (
      <>
        <ChatMessage role="user">{m.content}</ChatMessage>
        {/* 停止生成的提问没有回答，标一下，免得看起来像重复提问。 */}
        {props.stopped ? (
          <ChatMessage meta={formatTime(m.created_at)}>
            <div className="cb-muted">已停止生成</div>
            <RetryBar question={m.content} retry={props.retry} />
          </ChatMessage>
        ) : null}
      </>
    )
  const answer = legacyAnswer(m.content)
  // 历史回答的思考过程默认折叠，点开可看。
  const reasoning = <ReasoningPanel text={m.meta?.reasoning ?? ''} thinking={false} answering />
  if (hasResult) {
    const reply = replay.data
    const turn: Turn | null = reply
      ? { ...turnFromReply('', reply), messageId: m.id, queryRunId: m.query_run_id ?? null, sql: m.normalized_sql ?? undefined, chart: reply.chart ?? m.chart ?? undefined, meta: m.meta ?? reply.meta ?? undefined, reasoning: m.meta?.reasoning ?? '', answer }
      : null
    return (
      <ChatMessage meta={formatTime(m.created_at)}>
        {reasoning}
        {turn?.status === 'success' ? (
          <Answer turn={turn} conversationId={props.conversationId} onRun={props.onRun} running={props.running} limits={props.limits} feedback={props.feedback} onRate={props.onRate} />
        ) : turn ? (
          <>
            <ErrorNotice code={turn.errorCode} tone={turn.status === 'blocked' ? 'danger' : 'warning'} />
            {answer ? <div className="cb-answer">{answer}</div> : null}
            {turn.status === 'failed' ? <RetryBar question={props.question} retry={props.retry} /> : null}
          </>
        ) : replay.isPending && replay.fetchStatus !== 'idle' ? (
          <div className="cb-muted">正在读取结果…</div>
        ) : (
          <>
            {answer ? <div className="cb-answer">{answer}</div> : null}
            {replay.isError ? <Callout tone="warning">结果暂时读取失败。</Callout> : null}
            <div className="cb-row">
              <Button size="sm" variant="ghost" icon="table" onClick={() => setOpen(true)}>
                {replay.isError ? '重新读取' : '查看结果'}
              </Button>
            </div>
          </>
        )}
      </ChatMessage>
    )
  }
  const failed = codeFromMessage(m.content)
  return (
    <ChatMessage meta={formatTime(m.created_at)}>
      {reasoning}
      {m.clarification_question ? (
        <ClarifyPrompt question={m.clarification_question} options={m.clarification_choices ?? []} disabled={!props.onPick || props.running} picked={null} onPick={(label) => props.onPick?.(label)} />
      ) : failed ? (
        <>
          <ErrorNotice code={failed} tone={failed.startsWith('SQL_') && failed !== 'SQL_EXECUTION_ERROR' ? 'danger' : 'warning'} />
          {/* 被拦截的提问原样重问还是会被拦截，只给可能是临时故障的失败重试。 */}
          {failed.startsWith('SQL_') && failed !== 'SQL_EXECUTION_ERROR' ? null : <RetryBar question={props.question} retry={props.retry} />}
        </>
      ) : (
        <div className="cb-answer">{answer}</div>
      )}
    </ChatMessage>
  )
}

function TurnView(props: { turn: Turn; conversationId: number; onRun: (sql: string, sourceMessageId?: number | null) => void; running: boolean; onPick: (label: string) => void; limits?: ChatLimits; retry: RetryActions } & Rating) {
  const { turn } = props
  const live = turn.status === 'streaming'
  const answering = turn.answer.length > 0 || !live
  const thinking = live && turn.reasoning.length > 0 && turn.reasoningEndedAt == null
  const thoughtFor = turn.reasoningStartedAt != null && turn.reasoningEndedAt != null ? Math.max(1, Math.round((turn.reasoningEndedAt - turn.reasoningStartedAt) / 1000)) : undefined
  const stopped = turn.errorCode === 'QUERY_CANCELLED'
  const meta = live ? (
    <span>{STAGE_LABEL[turn.stages[turn.stages.length - 1] ?? ''] ?? '处理中'}</span>
  ) : turn.ms ? (
    <span>{stopped ? `已停止 · ${formatMs(turn.ms)}` : formatMs(turn.ms)}</span>
  ) : null
  // 结果到达后边写结论边展示；完成前不显示反馈和导出。
  const showAnswer = turn.status === 'success' || (live && (turn.answer.length > 0 || turn.columns.length > 0))
  return (
    <>
      <ChatMessage role="user">{turn.question}</ChatMessage>
      <ChatMessage meta={meta} streaming={live && !showAnswer && !thinking}>
        <ReasoningPanel text={turn.reasoning} thinking={thinking} answering={answering} seconds={thoughtFor} />
        {turn.status === 'clarify' && turn.clarification ? (
          <ClarifyPrompt
            question={turn.clarification.question}
            options={turn.clarification.choices}
            picked={turn.picked}
            disabled={turn.picked != null || turn.clarifyMessageId == null}
            onPick={props.onPick}
          />
        ) : null}
        {turn.status === 'blocked' || turn.status === 'failed' ? (
          <ErrorNotice code={turn.errorCode} tone={turn.status === 'blocked' ? 'danger' : 'warning'} />
        ) : null}
        {/* 手动执行 SQL 的失败要回到 SQL 改，不是重新提问。 */}
        {turn.status === 'failed' && turn.question !== MANUAL_RERUN ? <RetryBar question={turn.question} retry={props.retry} /> : null}
        {showAnswer ? <Answer turn={turn} conversationId={props.conversationId} onRun={props.onRun} running={props.running} limits={props.limits} feedback={props.feedback} onRate={props.onRate} /> : null}
      </ChatMessage>
    </>
  )
}

function exportHint(limits?: ChatLimits): string | undefined {
  if (!limits) return undefined
  const rows = limits.export_max_rows >= 10_000 ? `${limits.export_max_rows / 10_000} 万行` : `${limits.export_max_rows.toLocaleString('zh-CN')} 行`
  return `最多 ${rows}、${limits.export_max_mb} MB`
}

function Answer(props: { turn: Turn; conversationId: number; onRun: (sql: string, sourceMessageId?: number | null) => void; running: boolean; limits?: ChatLimits } & Rating) {
  const { turn } = props
  const toast = useToast()
  const live = turn.status === 'streaming'
  const messageId = turn.messageId
  const answer = turn.answer || (live ? '' : conclusion(turn.rowCount || turn.rows.length, turn.truncated))
  const exportFile = async (format: 'csv' | 'xlsx') => {
    if (messageId == null) return
    try {
      const result = await download(`/api/chat/conversations/${props.conversationId}/messages/${messageId}/export?format=${format}`, `result-${messageId}.${format}`)
      if (result.truncated)
        toast({ tone: 'warning', title: '导出已截断', message: `文件只包含前 ${(result.rows ?? 0).toLocaleString('zh-CN')} 行（${exportHint(props.limits) ?? '达到导出上限'}）。` })
    } catch (e) {
      toast({ tone: 'danger', title: '导出失败', message: describeError(e instanceof ApiError ? e.code : undefined) })
    }
  }
  return (
    <>
      {answer ? (
        <div className="cb-answer">
          {answer}
          {live && turn.answer ? <span className="cb-caret" aria-label="生成中" /> : null}
        </div>
      ) : null}
      {turn.columns.length && turn.rows.length ? (
        <ResultCard
          key={`result:${turn.sql}`}
          spec={turn.chart}
          columns={turn.columns}
          rows={turn.rows}
          total={turn.rowCount || turn.rows.length}
          truncated={turn.truncated}
          exportLimitHint={exportHint(props.limits)}
          onExport={live || messageId == null ? undefined : (format) => void exportFile(format)}
        />
      ) : null}
      {!live && turn.rows.length === 0 ? <EmptyResultHint where={extractWhereClause(turn.sql)} /> : null}
      {turn.sql ? <SqlBlock key={`sql:${turn.sql}`} sql={turn.sql} onRun={(sql) => props.onRun(sql, messageId)} running={props.running} /> : null}
      {live ? null : (
        <FeedbackBar
          // 查询日志过期后没有可关联的记录，不给出点了也提交不了的评价按钮。
          rateable={turn.queryRunId != null}
          initial={props.feedback}
          onSubmit={async (rating, reason, comment) => {
            if (turn.queryRunId != null) await props.onRate(turn.queryRunId, rating, reason, comment)
          }}
          onCopy={async () => {
            await navigator.clipboard?.writeText(answer)
          }}
        />
      )}
    </>
  )
}
