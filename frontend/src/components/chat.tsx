import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Badge, Button, Callout, ScopeTag, type Scope } from './basic'
import { friendlyError } from '../lib/errors'
import { columnFormat, formatValue, isNumericColumn, toNumber, type NumberFormat } from '../lib/format'
import { Icon } from './Icon'
import { Select } from './Select'
import type { FeedbackInput } from '../api/types'

const cx = (...parts: Array<string | false | null | undefined>) => parts.filter(Boolean).join(' ')

/* ---------- Composer ---------- */
export interface MentionMetric {
  id: number
  name: string
  aliases: string[]
}

/** 光标前最近一个 `@xxx`（xxx 内无空白）；没有则返回 null。 */
export function activeMention(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at < 0) return null
  const query = before.slice(at + 1)
  if (/\s/.test(query) || (at > 0 && !/\s/.test(before[at - 1] ?? ''))) return null
  return { start: at, query }
}

export function filterMetrics(metrics: MentionMetric[], query: string): MentionMetric[] {
  const q = query.trim().toLowerCase()
  return metrics.filter((m) => !q || m.name.toLowerCase().includes(q) || m.aliases.some((a) => a.toLowerCase().includes(q))).slice(0, 6)
}

/** 只认文本里仍然存在的 `@指标名`，用户删掉后就不再作为硬性命中。 */
export function mentionedIds(text: string, picked: MentionMetric[]): number[] {
  return picked.filter((m) => text.includes(`@${m.name}`)).map((m) => m.id)
}

/** 与后端 QuestionRequest.question 的上限一致。 */
const MAX_QUESTION = 10000

export function Composer(props: {
  source: string
  hint?: string
  streaming?: boolean
  disabled?: boolean
  metrics?: MentionMetric[]
  /** 已启用的 LLM；为空时不显示模型选择。 */
  models?: { id: number; name: string }[]
  modelId?: number | null
  onModelChange?: (id: number) => void
  onSubmit: (text: string, referencedMetricIds: number[]) => void
  onStop?: () => void
  /** 每个会话各自保存未发送的草稿（localStorage），切换会话回来还在。 */
  draftKey?: string
  /** 把一段文字放进输入框（「编辑后提问」）；nonce 变化才生效。 */
  fill?: { text: string; nonce: number }
}) {
  const storageKey = props.draftKey ? `askdata.draft.${props.draftKey}` : null
  const [text, setTextState] = useState(() => (storageKey ? localStorage.getItem(storageKey) ?? '' : ''))
  const setText = (value: string) => {
    setTextState(value)
    if (storageKey) {
      if (value) localStorage.setItem(storageKey, value)
      else localStorage.removeItem(storageKey)
    }
  }
  const area = useRef<HTMLTextAreaElement>(null)
  const [filled, setFilled] = useState(props.fill?.nonce)
  if (props.fill && props.fill.nonce !== filled) {
    setFilled(props.fill.nonce)
    setText(props.fill.text)
  }
  useEffect(() => {
    if (props.fill) area.current?.focus()
  }, [props.fill])
  const [picked, setPicked] = useState<MentionMetric[]>([])
  const [caret, setCaret] = useState(0)
  const [active, setActive] = useState(0)
  const mention = activeMention(text, caret)
  const options = mention ? filterMetrics(props.metrics ?? [], mention.query) : []
  const submit = () => {
    const value = text.trim()
    if (!value || props.streaming || props.disabled) return
    props.onSubmit(value, mentionedIds(value, picked))
    setText('')
    setPicked([])
  }
  const choose = (m: MentionMetric) => {
    if (!mention) return
    const next = `${text.slice(0, mention.start)}@${m.name} ${text.slice(caret)}`
    setText(next)
    setPicked((cur) => (cur.some((x) => x.id === m.id) ? cur : [...cur, m]))
    setActive(0)
  }
  return (
    <div className="cb-composer" style={{ position: 'relative' }}>
      {options.length ? (
        <ul className="cb-mention" role="listbox" aria-label="选择指标">
          {options.map((m, i) => (
            <li key={m.id} role="option" aria-selected={i === active}>
              <button type="button" onMouseDown={(e) => { e.preventDefault(); choose(m) }}>
                <Icon name="sigma" size="sm" />
                {m.name}
                {m.aliases.length ? <span className="cb-muted">{m.aliases.join('、')}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <textarea
        ref={area}
        rows={2}
        maxLength={MAX_QUESTION}
        value={text}
        placeholder="用自然语言提问，输入 @ 可以指定指标"
        aria-label="提问"
        onChange={(e) => {
          setText(e.target.value)
          setCaret(e.target.selectionStart)
          setActive(0)
        }}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
        onKeyDown={(e) => {
          if (options.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
            e.preventDefault()
            setActive((i) => (i + (e.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length)
          } else if (options.length && (e.key === 'Enter' || e.key === 'Tab') && !e.nativeEvent.isComposing) {
            e.preventDefault()
            const m = options[active]
            if (m) choose(m)
          } else if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <div className="cb-composer-bar">
        <span className="cb-source" title="会话创建时绑定，不可切换">
          <Icon name="database" size="sm" />
          {props.source}
          <Icon name="lock" size="sm" />
        </span>
        {props.hint ? <span className="cb-muted">{props.hint}</span> : null}
        {text.length > MAX_QUESTION * 0.9 ? <span className="cb-muted">{text.length}/{MAX_QUESTION}</span> : null}
        {props.models?.length ? (
          <Select
            size="sm"
            className="cb-model-select"
            ariaLabel="模型"
            value={props.modelId}
            onChange={(id) => props.onModelChange?.(id)}
            options={props.models.map((m) => ({ value: m.id, label: m.name }))}
          />
        ) : null}
        <span className="cb-spacer" />
        {props.streaming ? (
          <Button size="sm" icon="x" onClick={props.onStop}>
            停止
          </Button>
        ) : (
          <Button variant="primary" size="sm" icon="send" disabled={!text.trim() || props.disabled} onClick={submit}>
            发送
          </Button>
        )}
      </div>
    </div>
  )
}

/* ---------- ChatMessage ---------- */
export function ChatMessage(props: { role?: 'user' | 'assistant'; meta?: ReactNode; streaming?: boolean; children?: ReactNode }) {
  if (props.role === 'user')
    return (
      <div className="cb-msg cb-msg-user">
        <div className="cb-bubble">{props.children}</div>
      </div>
    )
  return (
    <div className="cb-msg">
      <div className="cb-avatar" aria-hidden="true">
        BI
      </div>
      <div className="cb-msg-body">
        {props.meta ? <div className="cb-msg-meta">{props.meta}</div> : null}
        {props.children}
        {props.streaming ? <span className="cb-caret" aria-label="生成中" /> : null}
      </div>
    </div>
  )
}

/* ---------- ClarifyPrompt ---------- */
export function ClarifyPrompt(props: { question: ReactNode; options: string[]; picked?: string | null; disabled?: boolean; onPick: (label: string) => void }) {
  return (
    <div className="cb-clarify" role="group" aria-label="澄清问题">
      <div className="cb-clarify-q">
        <Icon name="help" />
        <div>
          <b>需要确认一下 · </b>
          {props.question}
        </div>
      </div>
      <div className="cb-row">
        {props.options.map((label) => {
          const selected = props.picked === label
          return (
            <button
              key={label}
              type="button"
              className="cb-chip"
              aria-pressed={selected}
              disabled={props.disabled}
              onClick={() => props.onPick(label)}
            >
              {selected ? <Icon name="check" size="sm" /> : null}
              {label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/* ---------- ExplainCard ---------- */
export interface ExplainDefinition {
  kind: string
  name: string
  scope: string
  version?: number
  description?: string
}

/** 这样理解你的问题：口径、过滤条件、用到的语义/指标（带级别）和后端算好的时间范围。 */
export function ExplainCard(props: { summary?: ReactNode; filters?: string[]; definitions?: ExplainDefinition[]; timeRange?: string }) {
  const rows: [string, ReactNode][] = []
  if (props.definitions?.length)
    rows.push([
      '口径',
      <ul className="cb-explain-defs">
        {props.definitions.map((d) => (
          <li key={`${d.kind}:${d.name}:${d.scope}`}>
            <span>{d.name}</span>
            <ScopeTag level={(['platform', 'team', 'personal'].includes(d.scope) ? d.scope : 'platform') as Scope} />
            {d.description ? <span className="cb-muted">{d.description}</span> : null}
          </li>
        ))}
      </ul>,
    ])
  if (props.filters?.length)
    rows.push([
      '过滤',
      <>
        {props.filters.map((f) => (
          <span key={f} className="cb-explain-chip">
            {f}
          </span>
        ))}
      </>,
    ])
  if (props.timeRange) rows.push(['时间', <span>{props.timeRange}</span>])
  if (!props.summary && !rows.length) return null
  return (
    <div className="cb-explain">
      <div>
        <span className="cb-explain-title">这样理解你的问题</span>
        {props.summary}
      </div>
      {rows.length ? (
        <dl>
          {rows.map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  )
}

/* ---------- SqlBlock ---------- */
const KEYWORDS =
  /^(SELECT|FROM|WHERE|AND|OR|NOT|IN|AS|ON|JOIN|LEFT|RIGHT|INNER|OUTER|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|SUM|COUNT|AVG|MIN|MAX|DISTINCT|CASE|WHEN|THEN|ELSE|END|BETWEEN|IS|NULL|DESC|ASC|WITH|DATE_TRUNC|INTERVAL|CURRENT_DATE|LIKE|COALESCE|ROUND|CROSS|FULL|UNION|EXCEPT|INTERSECT)$/i

const CLAUSE =
  /^(WITH|SELECT|FROM|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|OFFSET|UNION(?:\s+ALL)?|EXCEPT|INTERSECT|(?:(?:LEFT|RIGHT|FULL|INNER|CROSS)\s+)?(?:OUTER\s+)?JOIN)\b/i

/**
 * 一行 SQL 排成多行：子句前换行，按子查询层级缩进；字符串和带引号的标识符原样保留。
 * 只有子查询（括号里以 SELECT/WITH 开头）里的子句换行；OVER (… ORDER BY …)、IN (…) 等保持在一行。
 */
export function formatSql(sql: string): string {
  let out = ''
  // 每层括号是否是子查询。
  const parens: boolean[] = []
  const depth = () => parens.filter(Boolean).length
  let i = 0
  while (i < sql.length) {
    const ch = sql[i] as string
    if (ch === "'" || ch === '"' || ch === '`') {
      let j = i + 1
      while (j < sql.length) {
        if (sql[j] === ch && sql[j + 1] === ch) j += 2
        else if (sql[j] === ch) break
        else j += 1
      }
      out += sql.slice(i, j + 1)
      i = j + 1
      continue
    }
    if (/\s/.test(ch)) {
      while (i < sql.length && /\s/.test(sql[i] as string)) i += 1
      if (out && !/[\s(]$/.test(out)) out += ' '
      continue
    }
    if (/[A-Za-z]/.test(ch) && (i === 0 || !/[\w$]/.test(sql[i - 1] as string))) {
      const match = CLAUSE.exec(sql.slice(i))
      if (match) {
        const keyword = match[0].toUpperCase().replace(/\s+/g, ' ')
        const opensSubquery = parens.length > 0 && out.endsWith('(') && /^(SELECT|WITH)$/.test(keyword)
        if (opensSubquery) parens[parens.length - 1] = true
        if (parens.length === 0 || parens[parens.length - 1]) {
          out = out.trimEnd()
          if (out) out += `\n${'  '.repeat(depth())}`
        }
        out += keyword
        i += match[0].length
        continue
      }
    }
    if (ch === '(') parens.push(false)
    if (ch === ')') parens.pop()
    out += ch
    i += 1
  }
  return out.trim()
}

export function highlightSql(sql: string): ReactNode[] {
  const re = /(--[^\n]*)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\s+)|([^\sA-Za-z0-9_']+)/g
  const out: ReactNode[] = []
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(sql))) {
    i++
    if (m[1]) out.push(<span key={i} className="cb-cm">{m[1]}</span>)
    else if (m[2]) out.push(<span key={i} className="cb-str">{m[2]}</span>)
    else if (m[3]) out.push(<span key={i} className="cb-nm">{m[3]}</span>)
    else if (m[4] && KEYWORDS.test(m[4])) out.push(<span key={i} className="cb-kw">{m[4]}</span>)
    else out.push(m[0])
  }
  return out
}

export function SqlBlock(props: { sql: string; onRun?: (sql: string) => void; running?: boolean; defaultOpen?: boolean; rejected?: boolean }) {
  // 编辑和复制都用排好版的 SQL；重新执行时后端仍会重新校验。
  const original = formatSql(props.sql)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(original)
  const [copied, setCopied] = useState(false)
  // 业务用户多数不看 SQL：默认收成一行，点开看全文。
  const [open, setOpen] = useState(props.defaultOpen ?? false)
  const area = useRef<HTMLTextAreaElement>(null)
  const changed = text !== original

  // 编辑框跟随内容长高：进入编辑时与展开的全文同高，不出现内部滚动条。
  useLayoutEffect(() => {
    const el = area.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [text, editing, open])

  const cancel = () => {
    setText(original)
    setEditing(false)
  }

  return (
    <div className="cb-sql" data-open={open}>
      <div className="cb-sql-head">
        <button type="button" className="cb-sql-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          <Icon name="chevronDown" size="sm" className="cb-sql-chevron" />
          <span className="cb-eyebrow">SQL</span>
        </button>
        {props.rejected ? (
          <Badge tone="danger" icon="shield">
            被拦截 · 未执行
          </Badge>
        ) : (
          <Badge tone="success" icon="shield">
            只读 · 已校验
          </Badge>
        )}
        {editing && changed ? (
          <Badge tone="warning" icon="edit">
            已手动修改
          </Badge>
        ) : null}
        <span className="cb-spacer" />
        {open && !editing ? (
          <Button
            variant="ghost"
            size="sm"
            icon={copied ? 'check' : 'copy'}
            aria-label="复制 SQL"
            onClick={() => {
              void navigator.clipboard?.writeText(text)
              setCopied(true)
            }}
          />
        ) : null}
        {open && editing ? (
          <>
            <Button variant="ghost" size="sm" onClick={cancel}>
              取消
            </Button>
            <Button
              variant="primary"
              size="sm"
              icon="play"
              disabled={props.running || !text.trim()}
              onClick={() => {
                // 结果会作为新的一轮回答出现；这里回到原 SQL，块可以照常折叠。
                props.onRun?.(text)
                cancel()
              }}
            >
              重新执行
            </Button>
          </>
        ) : props.onRun && open ? (
          <Button variant="ghost" size="sm" icon="edit" disabled={props.running} onClick={() => setEditing(true)}>
            编辑
          </Button>
        ) : null}
      </div>
      {open && editing ? (
        <textarea
          ref={area}
          value={text}
          spellCheck={false}
          aria-label="编辑 SQL"
          autoFocus
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') cancel()
          }}
        />
      ) : open ? (
        <pre>
          <code>{highlightSql(text)}</code>
        </pre>
      ) : null}
    </div>
  )
}

/* ---------- ResultTable ---------- */
export function formatCell(value: unknown, fmt?: NumberFormat): ReactNode {
  if (value == null) return <span className="cb-muted">—</span>
  if (fmt && toNumber(value) !== null) return formatValue(value, fmt)
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function ResultTable(props: {
  columns: string[]
  rows: Record<string, unknown>[]
  truncated?: boolean
  /** 服务端实际取回的总行数；超过展示行数时提示用导出获取全部。 */
  total?: number
  ms?: number
  maxHeight?: number
  labels?: Record<string, string>
  formats?: Record<string, NumberFormat>
  onExport?: (format: 'csv' | 'xlsx') => void
}) {
  // 只有真正的数字列才右对齐并格式化；数字形式的编号、手机号保持原样。
  const numeric = new Set(props.columns.filter((c) => isNumericColumn(props.rows, c)))
  const getFormat = (col: string) => columnFormat(col, props.rows, props.formats)

  return (
    <div className="cb-table-wrap" style={props.maxHeight ? { maxHeight: props.maxHeight } : undefined}>
      <table className="cb-table">
        <thead>
          <tr>
            {props.columns.map((c) => (
              <th key={c} className={numeric.has(c) ? 'cb-r' : undefined}>
                {props.labels?.[c] ?? c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {props.rows.map((row, i) => (
            <tr key={i}>
              {props.columns.map((c) => (
                <td key={c} className={numeric.has(c) ? 'cb-r' : undefined}>
                  {formatCell(row[c], numeric.has(c) ? getFormat(c) : undefined)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="cb-table-foot">
        <span className="cb-num">
          {(props.total ?? props.rows.length).toLocaleString('zh-CN')} 行
          {props.total != null && props.total > props.rows.length ? `，显示前 ${props.rows.length.toLocaleString('zh-CN')} 行，导出可获取全部` : ''}
        </span>
        {props.truncated ? (
          <Badge tone="warning" icon="alert">
            已截断，仅返回部分行
          </Badge>
        ) : null}
        {props.ms != null ? <span className="cb-num">· {props.ms} ms</span> : null}
        <span className="cb-spacer" />
        {props.onExport ? (
          <>
            <Button variant="ghost" size="sm" icon="download" onClick={() => props.onExport?.('csv')}>
              CSV
            </Button>
            <Button variant="ghost" size="sm" icon="download" onClick={() => props.onExport?.('xlsx')}>
              Excel
            </Button>
          </>
        ) : null}
      </div>
    </div>
  )
}

export { ChartCard, chartData, chartModel, pieEligible } from './charts'
export type { ChartModel } from './charts'

/* ---------- FeedbackBar ---------- */
const REASONS: { label: string; code: NonNullable<FeedbackInput['reason_code']> }[] = [
  { label: '口径不对（指标或别名理解错）', code: 'metric_wrong' },
  { label: '选错了表或字段', code: 'table_wrong' },
  { label: '结果数据不对', code: 'result_wrong' },
  { label: 'SQL 报错或超时', code: 'other' },
  { label: '其他', code: 'other' },
]

export type FeedbackRating = 'up' | 'down'

/**
 * 有用/没用与复制。每个回答每人一条评价，可以改；刷新后按 initial 显示已选状态。
 * rateable=false（查询日志已过期等）时只保留复制，不给出无效的评价按钮。
 */
export function FeedbackBar(props: {
  onSubmit: (rating: FeedbackRating, reason?: FeedbackInput['reason_code'], comment?: string) => Promise<void>
  onCopy?: () => Promise<void> | void
  initial?: { rating: FeedbackRating; reason_code?: string | null; comment?: string } | null
  rateable?: boolean
}) {
  const rateable = props.rateable ?? true
  const [value, setValue] = useState<FeedbackRating | null>(props.initial?.rating ?? null)
  const [open, setOpen] = useState(false)
  const [pick, setPick] = useState(() => Math.max(0, REASONS.findIndex((r) => r.label === props.initial?.comment || r.code === props.initial?.reason_code)))
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const [copied, setCopied] = useState(false)
  // 评价在刷新后才读到时同步一次，用户已经点过的不覆盖。
  const initialRating = props.initial?.rating ?? null
  const [synced, setSynced] = useState(initialRating)
  if (initialRating !== synced) {
    setSynced(initialRating)
    if (value == null) setValue(initialRating)
  }
  const submit = async (rating: FeedbackRating, reason?: FeedbackInput['reason_code'], comment?: string) => {
    const before = value
    setBusy(true)
    setError(false)
    setValue(rating)
    try {
      await props.onSubmit(rating, reason, comment)
      setOpen(false)
    } catch {
      setValue(before)
      setError(true)
    } finally {
      setBusy(false)
    }
  }
  const copy = async () => {
    await props.onCopy?.()
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }
  const reason = REASONS[pick]
  return (
    <div>
      <div className="cb-feedback">
        {rateable ? (
          <>
            <Button variant="ghost" size="sm" icon="thumbsUp" className="cb-fb-up" aria-pressed={value === 'up'} disabled={busy} onClick={() => (value === 'up' ? undefined : void submit('up'))}>
              有用
            </Button>
            <Button variant="ghost" size="sm" icon="thumbsDown" aria-pressed={value === 'down'} disabled={busy} onClick={() => setOpen((o) => !o)}>
              没用
            </Button>
          </>
        ) : null}
        {props.onCopy ? (
          <Button variant="ghost" size="sm" icon={copied ? 'check' : 'copy'} aria-label="复制答案" onClick={() => void copy()}>
            {copied ? '已复制' : '复制'}
          </Button>
        ) : null}
        {error ? (
          <span className="cb-feedback-note cb-feedback-error">提交失败，请重试</span>
        ) : value && !open ? (
          <span className="cb-feedback-note">{value === 'down' ? '已提交，团队负责人会处理；可以改选' : '感谢反馈'}</span>
        ) : null}
      </div>
      {open ? (
        <div className="cb-feedback-pop" role="dialog" aria-label="点踩原因">
          <div className="cb-h">哪里不对？</div>
          {REASONS.map((r, i) => (
            <label key={r.label} className="cb-radio">
              <input type="radio" name="cb-fb" checked={pick === i} onChange={() => setPick(i)} />
              {r.label}
            </label>
          ))}
          <textarea
            className="cb-feedback-text"
            rows={2}
            maxLength={500}
            aria-label="补充说明"
            placeholder={reason?.label === '其他' ? '说说哪里不对（必填）' : '补充说明（可选），例如期望的口径或正确的数字'}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="cb-muted">会附带本轮的问题、SQL 和结果摘要（行数与前 5 行）。</div>
          <div className="cb-row" style={{ justifyContent: 'flex-end' }}>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={busy || (reason?.label === '其他' && !note.trim())}
              onClick={() => void submit('down', reason?.code, note.trim() ? `${reason?.label}：${note.trim()}` : reason?.label)}
            >
              提交反馈
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

/* ---------- RunList (query log rows) ---------- */
export type RunStatus = 'ok' | 'blocked' | 'error' | 'fixed' | 'clarify' | 'cancelled'
const RUN_TONE: Record<RunStatus, ['success' | 'danger' | 'warning' | 'info' | 'neutral', string, 'check' | 'shield' | 'x' | 'refresh' | 'help']> = {
  ok: ['success', '成功', 'check'],
  blocked: ['danger', '已拦截', 'shield'],
  error: ['danger', '失败', 'x'],
  fixed: ['warning', '纠错后成功', 'refresh'],
  clarify: ['info', '待澄清', 'help'],
  cancelled: ['neutral', '已取消', 'x'],
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const [tone, label, icon] = RUN_TONE[status]
  return (
    <Badge tone={tone} icon={icon}>
      {label}
    </Badge>
  )
}

export function RunList(props: {
  rows: { key: number; status: RunStatus; question: string; reason?: string; detail?: string; user?: string; id: string; time: string }[]
  onOpen?: (key: number) => void
}) {
  return (
    <table className="cb-runs">
      <thead>
        <tr>
          <th>状态</th>
          <th>问题</th>
          {props.rows.some((r) => r.user != null) ? <th>用户</th> : null}
          <th>编号</th>
          <th>时间</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {props.rows.map((r) => (
          <tr key={r.key}>
            <td style={{ width: 1 }}>
              <RunStatusBadge status={r.status} />
            </td>
            <td>
              <div className="q">{r.question}</div>
              {r.reason ? <div className="reason">{r.reason}</div> : <div className={cx('cb-muted')}>{r.detail}</div>}
            </td>
            {r.user != null ? <td className="t">{r.user}</td> : null}
            <td className="t cb-mono">{r.id}</td>
            <td className="t">{r.time}</td>
            <td style={{ textAlign: 'right', width: 1, whiteSpace: 'nowrap' }}>
              <button type="button" className="cb-link" onClick={() => props.onOpen?.(r.key)}>
                查看
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* ---------- EmptyResultHint ---------- */
export function EmptyResultHint({ where }: { where: string | null }) {
  return (
    <Callout tone="warning" icon="help" title="查询成功，但没有匹配到任何记录">
      {where ? (
        <>
          <div>AI 是按下面这个条件查的，确认一下筛选词对不对：</div>
          <code className="code-sm cb-hint-code">{where}</code>
        </>
      ) : null}
      <div>
        可能是筛选词和库里的写法对不上（比如「华东」实际存的是「华东区」）、时间范围太窄或状态值不同。可以换个说法、放宽条件，或先问「某字段都有哪些取值」来对齐口径。
      </div>
    </Callout>
  )
}

/* ---------- ErrorNotice ---------- */
export function ErrorNotice({ code, tone }: { code: string | undefined; tone: 'danger' | 'warning' }) {
  const f = friendlyError(code)
  return (
    <Callout tone={tone} title={f.title}>
      {f.hint ? <div>{f.hint}</div> : null}
      {f.showCode ? (
        <div className="cb-muted">
          错误码：<code className="code-sm">{f.code}</code>
        </div>
      ) : null}
    </Callout>
  )
}

/* ---------- SuggestedQuestions ---------- */
export function SuggestedQuestions({ questions, onPick, disabled }: { questions: string[]; onPick: (q: string) => void; disabled?: boolean }) {
  if (!questions.length) return null
  return (
    <div className="cb-row" role="group" aria-label="猜你想问">
      {questions.map((q) => (
        <button key={q} type="button" className="cb-chip" disabled={disabled} onClick={() => onPick(q)}>
          {q}
        </button>
      ))}
    </div>
  )
}
