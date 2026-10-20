import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { draftSemantics, runSync, useDatasources, useSaveSetup, useSetup } from '../api/hooks'
import type { TableRead } from '../api/types'
import { Badge, Button, Callout, Segmented, TextField } from '../components/basic'
import { Check } from '../components/Check'
import { ConfirmDialog } from '../components/Dialog'
import { Icon } from '../components/Icon'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { HeaderActions } from '../components/PageHeader'
import { Pager, usePaged } from '../components/Pager'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { initialState, missing, missingIds, sameState, setupPayload, stageOf, type SetupState } from '../lib/setup'
import { useUnsavedGuard } from '../state/unsaved'

type Filter = 'all' | 'missing' | 'excluded'

const STEPS = [
  { at: 10, label: '连接数据库' },
  { at: 70, label: '读取表、字段和注释' },
  { at: 90, label: '整理并保存' },
]

/** 数据源接入的第二步（管理员）：读取表结构，选择导入的表，给每个字段补齐语义。 */
export function DatasourceSetupPage() {
  const { id } = useParams()
  const dsId = Number(id)
  const navigate = useNavigate()
  const location = useLocation()
  const qc = useQueryClient()
  const toast = useToast()
  const source = useDatasources().data?.items.find((d) => d.id === dsId)
  const setup = useSetup(dsId)
  const save = useSaveSetup(dsId)
  const read = setup.data

  const [reading, setReading] = useState<{ percent: number; error?: string } | null>(null)
  const started = useRef(false)
  const readSchema = useCallback(async () => {
    setReading({ percent: 5 })
    try {
      const r = await runSync(dsId, (p) => setReading({ percent: Math.max(5, p) }))
      if (r.status !== 'success') {
        setReading({ percent: 0, error: r.error ? describeError(r.error) : '请检查连接后重试。' })
        return
      }
      await setup.refetch()
      void qc.invalidateQueries({ queryKey: ['setup-summary'] })
      void qc.invalidateQueries({ queryKey: ['tables', dsId] })
      setReading(null)
    } catch (e) {
      setReading({ percent: 0, error: e instanceof ApiError ? describeError(e.code) : '请稍后重试。' })
    }
  }, [dsId, qc, setup])
  // 新建数据源进来时（或还从没读过表结构）自动读取一次。
  const fresh = (location.state as { sync?: boolean } | null)?.sync
  useEffect(() => {
    if (started.current || !setup.isSuccess) return
    if (fresh || read?.tables.length === 0) {
      started.current = true
      navigate(location.pathname, { replace: true, state: null })
      // After this render: reading sets state.
      void Promise.resolve().then(readSchema)
    }
  }, [fresh, read, setup.isSuccess, readSchema, navigate, location.pathname])

  const base = useMemo(() => initialState(read), [read])
  const [draft, setDraft] = useState<{ at: number; state: SetupState } | null>(null)
  const state = draft?.at === setup.dataUpdatedAt ? draft.state : base
  const edit = (next: SetupState) => setDraft({ at: setup.dataUpdatedAt, state: next })
  const dirty = !sameState(state, base)
  const gaps = missing(read, state)
  const [drafted, setDrafted] = useState<Set<number>>(new Set())
  const [drafting, setDrafting] = useState(false)
  const [dropping, setDropping] = useState<TableRead[] | null>(null)

  const [filter, setFilter] = useState<Filter>('all')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Set<number>>(new Set())
  const needle = q.trim().toLowerCase()
  const all = read?.tables ?? []
  const rows = all
    .map((t) => {
      const nameHit = !needle || `${t.name} ${t.comment}`.toLowerCase().includes(needle)
      const cols = nameHit ? (t.columns ?? []) : (t.columns ?? []).filter((c) => `${c.name} ${c.comment}`.toLowerCase().includes(needle))
      return { t, cols, forced: !nameHit || (filter === 'missing' && !!gaps.byTable[t.id]) }
    })
    .filter(({ t, cols, forced }) => (needle ? cols.length > 0 || !forced : true) && (filter === 'missing' ? !!gaps.byTable[t.id] : filter === 'excluded' ? !state.include[t.id] : true))
  const paged = usePaged(rows, 30)
  const chosen = all.filter((t) => state.include[t.id]).length
  // 已发布且没有改动：表的导入状态和勾选一致，也没有等待语义的字段。
  const settled = !dirty && all.every((t) => (state.include[t.id] ? stageOf(t) === 'imported' && !(t.columns ?? []).some((c) => c.status === 'pending') : stageOf(t) !== 'imported'))

  const persist = async (publish: boolean): Promise<boolean> => {
    try {
      const result = await save.mutateAsync(setupPayload(read, state, publish))
      setDraft(null)
      setDrafted(new Set())
      if (publish) {
        toast({ tone: 'success', title: `${source?.name ?? '数据源'}：表和语义已生效`, message: `这次导入 ${result.imported_tables} 张表。下一步：设置哪些团队可以查询它。` })
        navigate(`/datasources?detail=${dsId}`)
      } else {
        toast({ tone: 'success', title: '已保存', message: gaps.total ? `还差 ${gaps.total} 个字段的语义；完成前，新导入的表员工看不到。` : '可以点「完成并启用」了。' })
      }
      return true
    } catch (e) {
      toast({ tone: 'danger', title: publish ? '还不能启用' : '保存失败', message: e instanceof ApiError ? e.message || describeError(e.code) : '请稍后重试。' })
      return false
    }
  }
  const persistRef = useRef(persist)
  useEffect(() => {
    persistRef.current = persist
  })
  const guardSave = useCallback(() => persistRef.current(false), [])
  useUnsavedGuard(dirty, guardSave)

  const publish = () => {
    const dropped = all.filter((t) => stageOf(t) === 'imported' && !state.include[t.id])
    if (dropped.length) setDropping(dropped)
    else void persist(true)
  }

  const fillWithModel = async () => {
    const ids = missingIds(read, state)
    if (!ids.length) return
    setDrafting(true)
    try {
      const found: Record<number, string> = {}
      for (let i = 0; i < ids.length; i += 300) Object.assign(found, (await draftSemantics(dsId, ids.slice(i, i + 300))).descriptions)
      const next = { ...state, columns: { ...state.columns } }
      const filled = new Set(drafted)
      for (const [cid, text] of Object.entries(found)) {
        if (!next.columns[Number(cid)]?.trim()) {
          next.columns[Number(cid)] = text
          filled.add(Number(cid))
        }
      }
      edit(next)
      setDrafted(filled)
      const left = ids.length - Object.keys(found).length
      toast({ tone: 'success', title: `AI 起草了 ${Object.keys(found).length} 个字段的语义`, message: `标着「AI」的请逐个确认。${left ? `另有 ${left} 个字段看名字推断不出含义，需要手动填写。` : ''}` })
    } catch (e) {
      const code = e instanceof ApiError ? e.code : ''
      toast({ tone: 'danger', title: 'AI 补全失败', message: code === 'LLM_UNAVAILABLE' ? '没有可用的大模型，或模型暂时不可用。可以先在「模型配置」里添加。' : describeError(code) })
    } finally {
      setDrafting(false)
    }
  }

  const setColumn = (cid: number, text: string) => {
    edit({ ...state, columns: { ...state.columns, [cid]: text } })
    if (drafted.has(cid)) setDrafted((cur) => new Set([...cur].filter((x) => x !== cid)))
  }
  const toggle = (tid: number) =>
    setOpen((cur) => {
      const next = new Set(cur)
      if (!next.delete(tid)) next.add(tid)
      return next
    })

  const title = (
    <div className="cb-setup-crumb">
      <button type="button" className="cb-link" onClick={() => navigate('/datasources')}>
        数据源
      </button>
      <Icon name="chevronRight" size="sm" />
      <span>{source?.name ?? `数据源 ${dsId}`}</span>
      <Icon name="chevronRight" size="sm" />
      <b>表和语义</b>
    </div>
  )

  if (reading) {
    return (
      <div className="cb-page">
        {title}
        <section className="cb-panel cb-setup-reading" aria-label="读取表结构">
          {reading.error ? (
            <Callout tone="danger" title="读取表结构失败">
              {reading.error}
            </Callout>
          ) : (
            <>
              <h2>正在读取表结构</h2>
              <p className="cb-muted">读取每张表和每个字段，以及数据库里的注释。读到的注释会作为字段的默认语义。表多的库可能要一两分钟，可以先离开，稍后回到这里继续。</p>
              <div className="cb-meter-row">
                <div className="cb-meter" role="progressbar" aria-valuenow={reading.percent} aria-valuemin={0} aria-valuemax={100} aria-label="读取进度">
                  <i style={{ width: `${reading.percent}%` }} />
                </div>
                <span className="cb-meter-label">{reading.percent}%</span>
              </div>
              <ol className="cb-setup-steps">
                {STEPS.map((s, i) => {
                  const done = reading.percent >= (STEPS[i + 1]?.at ?? 100)
                  const now = !done && reading.percent >= s.at
                  return (
                    <li key={s.label} data-state={done ? 'done' : now ? 'now' : 'todo'}>
                      <Icon name={done ? 'check' : now ? 'refresh' : 'chevronRight'} size="sm" />
                      {s.label}
                    </li>
                  )
                })}
              </ol>
            </>
          )}
          {reading.error ? (
            <div className="cb-actions">
              <Button onClick={() => navigate('/datasources')}>返回数据源</Button>
              <Button variant="primary" icon="refresh" onClick={() => void readSchema()}>
                重新读取
              </Button>
            </div>
          ) : null}
        </section>
      </div>
    )
  }

  return (
    <div className="cb-page">
      <HeaderActions>
        <Button icon="refresh" disabled={dirty} title={dirty ? '先保存当前修改' : undefined} onClick={() => void readSchema()}>
          重新读取表结构
        </Button>
      </HeaderActions>
      {title}
      {read?.require_semantics === false ? (
        <Callout tone="info">这个数据源接入得比较早，不强制每个字段都有语义。补上语义能让回答更准，也能让员工看懂字段。</Callout>
      ) : (
        <Callout tone="info">勾选要导入的表。导入的表每个字段都要有语义：读到数据库注释的已经填好，可以修改；没有注释的需要补上。不导入的表员工和 AI 都看不到，以后可以再导入。</Callout>
      )}
      <div className="cb-filters">
        <Segmented
          label="筛选表"
          value={filter}
          onChange={(v) => {
            setFilter(v)
            paged.setPage(1)
          }}
          items={[
            { id: 'all', label: <>全部 <span className="cb-num">{all.length}</span></> },
            { id: 'missing', label: <>待补充 <span className="cb-num">{Object.keys(gaps.byTable).length}</span></> },
            { id: 'excluded', label: <>不导入 <span className="cb-num">{all.length - chosen}</span></> },
          ]}
        />
        <div className="cb-search">
          <TextField
            aria-label="筛选表或字段"
            placeholder="按表名、字段名或注释筛选"
            icon="search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              paged.setPage(1)
            }}
          />
        </div>
        <span className="cb-spacer" />
        <span className={gaps.total ? 'cb-setup-gap' : 'cb-faint'}>
          已选 {chosen}/{all.length} 张表 · {gaps.total ? `还差 ${gaps.total} 个字段的语义` : '语义已补齐'}
        </span>
        <Button icon="sparkle" disabled={!gaps.total || drafting} onClick={() => void fillWithModel()}>
          {drafting ? 'AI 起草中…' : 'AI 补全'}
        </Button>
        <Button disabled={!dirty || save.isPending} onClick={() => void persist(false)}>
          保存草稿
        </Button>
        <Button variant="primary" disabled={gaps.total > 0 || chosen === 0 || settled || save.isPending} title={gaps.total ? `还有 ${gaps.total} 个字段没有语义` : chosen === 0 ? '至少导入一张表' : settled ? '已全部生效，没有新的改动' : undefined} onClick={publish}>
          完成并启用
        </Button>
      </div>
      <ListCard label="表和语义" pager={<Pager {...paged.pager} />}>
        <section className="cb-pm-tree cb-setup-tree" aria-label="表和字段">
          <div className="cb-pm-head" role="row">
            <span>表 / 字段</span>
            <span>数据库注释</span>
            <span>语义</span>
            <span>导入</span>
          </div>
          <ListStatus
            loading={setup.isPending}
            error={setup.error}
            onRetry={() => void setup.refetch()}
            what="表"
            count={rows.length}
            filtered={!!needle || filter !== 'all'}
            query={needle ? q.trim() : undefined}
            onClear={() => {
              setQ('')
              setFilter('all')
            }}
            empty={<EmptyState icon="database" title="没有读到表">确认连接账号能看到这个 Schema 下的表，然后重新读取表结构。</EmptyState>}
          >
            {paged.rows.map(({ t, cols, forced }) => {
              const included = !!state.include[t.id]
              const gap = gaps.byTable[t.id] ?? 0
              const expanded = forced || open.has(t.id)
              const stage = stageOf(t)
              return (
                <div key={t.id} className={included ? 'cb-pm-table' : 'cb-pm-table cb-row-off'}>
                  <div className="cb-pm-row">
                    <button type="button" className="cb-pm-toggle" aria-expanded={expanded} onClick={() => toggle(t.id)}>
                      <Icon name="chevronDown" size="sm" />
                      <span className="cb-m cb-pm-name" title={`${t.schema_name}.${t.name}`}>{t.name}</span>
                      {gap ? <Badge tone="danger">缺 {gap}</Badge> : <Badge>{(t.columns ?? []).length} 个字段</Badge>}
                      {stage === 'imported' ? <Badge tone="success">已导入</Badge> : null}
                    </button>
                    <span className="cb-faint cb-setup-note" title={t.comment}>{t.comment || '—'}</span>
                    <input className="cb-sem-in" aria-label={`${t.name} 表的语义`} placeholder="这张表记录什么（选填）" value={state.tables[t.id] ?? ''} onChange={(e) => edit({ ...state, tables: { ...state.tables, [t.id]: e.target.value } })} />
                    <Check state={included ? 'on' : 'off'} label={`导入 ${t.name}`} onChange={(on) => edit({ ...state, include: { ...state.include, [t.id]: on } })} />
                  </div>
                  {expanded
                    ? cols.map((c) => {
                        const value = state.columns[c.id] ?? ''
                        const empty = included && !value.trim()
                        return (
                          <div key={c.id} className="cb-pm-row cb-pm-col">
                            <span className="cb-setup-field">
                              <span className="cb-m" title={c.name}>{c.name}</span>
                              <Badge mono>{c.data_type}</Badge>
                            </span>
                            <span className="cb-faint cb-setup-note" title={c.comment}>{c.comment || '—'}</span>
                            <span className="cb-setup-input">
                              <input className="cb-sem-in" aria-label={`${t.name}.${c.name} 的语义`} aria-invalid={empty || undefined} placeholder={empty ? '必填：这个字段是什么' : '这个字段是什么'} value={value} onChange={(e) => setColumn(c.id, e.target.value)} />
                              {drafted.has(c.id) ? (
                                <span title="AI 起草，确认或修改后再保存">
                                  <Badge tone="info">AI</Badge>
                                </span>
                              ) : null}
                            </span>
                            <span />
                          </div>
                        )
                      })
                    : null}
                </div>
              )
            })}
          </ListStatus>
        </section>
      </ListCard>
      {dropping ? (
        <ConfirmDialog
          title="取消导入已导入的表"
          confirmLabel="取消导入并启用"
          danger
          busy={save.isPending}
          onClose={() => setDropping(null)}
          onConfirm={() => {
            setDropping(null)
            void persist(true)
          }}
        >
          {dropping.map((t) => t.name).join('、')} 已经导入，取消后员工和 AI 会立即看不到它们，团队的授权也会一并撤销。以后可以再导入。
        </ConfirmDialog>
      ) : null}
    </div>
  )
}
