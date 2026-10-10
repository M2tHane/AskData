import { useState } from 'react'
import { useDeleteMetric, useMetrics, usePromote, usePromotions, useReviewPromotion, useSaveMetric } from '../api/hooks'
import type { MetricInput, MetricView, PromotionView, Role } from '../api/types'
import { Badge, Button, Callout, ScopeTag, Segmented, Tabs, TextField } from '../components/basic'
import { DatasourcePicker } from '../components/DatasourcePicker'
import { ConfirmDialog, Dialog, DialogFooter } from '../components/Dialog'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { Pager, usePaged } from '../components/Pager'
import { HeaderActions } from '../components/PageHeader'
import { Select } from '../components/Select'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { FEATURES } from '../lib/features'
import { formatTime } from '../lib/format'
import { useMetricRefs } from '../lib/metricsRefs'
import { removedReferences } from '../lib/metricsValidity'
import { OWN_SCOPE, SCOPE_NAME, canEdit } from '../lib/scope'
import { useSchemaChanges } from '../state/schemaChanges'

type Tab = 'metrics' | 'requests'
type LevelFilter = 'all' | 'platform' | 'team' | 'personal'
type Sort = 'default' | 'refs'

const REQUEST_STATUS = { pending: { tone: 'warning', label: '待审核' }, approved: { tone: 'success', label: '已通过' }, rejected: { tone: 'danger', label: '已驳回' } } as const

export function MetricsPage({ role }: { role: Role }) {
  const toast = useToast()
  const [ds, setDs] = useState<number | null>(null)
  const [tab, setTab] = useState<Tab>('metrics')
  const [editing, setEditing] = useState<{ id?: number; form: MetricInput } | null>(null)
  const save = useSaveMetric(ds)
  const list = useMetrics(ds)
  const promotions = usePromotions()
  const pending = (promotions.data ?? []).filter((p) => p.status === 'pending')

  return (
    <div className="cb-page">
      {tab === 'metrics' ? (
        <HeaderActions>
          <DatasourcePicker value={ds} onChange={setDs} />
          <Button
            variant="primary"
            icon="plus"
            disabled={ds == null}
            onClick={() => ds != null && setEditing({ form: { datasource_id: ds, name: '', aliases: [], description: '', sql_template: '', params: {}, scope: OWN_SCOPE[role] } })}
          >
            添加指标
          </Button>
        </HeaderActions>
      ) : null}
      <Tabs
        label="指标子模块"
        value={tab}
        onChange={(id) => setTab(id as Tab)}
        items={[
          { id: 'metrics', label: '指标', count: list.data?.length },
          { id: 'requests', label: '申请', count: pending.length || undefined },
        ]}
      />
      {tab === 'metrics' ? (
        <MetricsTab role={role} ds={ds} list={list} onAdd={(form) => setEditing({ form })} onEdit={(id, form) => setEditing({ id, form })} />
      ) : (
        <RequestsTab role={role} ds={ds} metrics={list.data ?? []} promotions={promotions} />
      )}
      {editing ? (
        <Dialog title={editing.id ? '编辑指标' : '添加指标'} onClose={() => setEditing(null)}>
          <MetricForm
            value={editing.form}
            busy={save.isPending}
            error={save.error instanceof ApiError ? save.error.message : null}
            onCancel={() => setEditing(null)}
            onSave={(form) =>
              save.mutate(
                { id: editing.id, body: form },
                {
                  onSuccess: () => {
                    toast({ tone: 'success', title: editing.id ? `已保存「${form.name}」` : `已添加「${form.name}」`, message: '口径已用只读账号试跑校验。' })
                    setEditing(null)
                  },
                },
              )
            }
          />
        </Dialog>
      ) : null}
    </div>
  )
}

function MetricsTab(props: {
  role: Role
  ds: number | null
  list: ReturnType<typeof useMetrics>
  onAdd: (form: MetricInput) => void
  onEdit: (id: number, form: MetricInput) => void
}) {
  const { role, ds, list } = props
  const toast = useToast()
  const del = useDeleteMetric(ds)
  const promote = usePromote()
  const changes = useSchemaChanges((s) => (ds == null ? undefined : s.byDatasource[ds]))
  const refs = useMetricRefs(ds)
  const [q, setQ] = useState('')
  const [level, setLevel] = useState<LevelFilter>('all')
  const [sort, setSort] = useState<Sort>('default')
  const [deleting, setDeleting] = useState<MetricView | null>(null)

  const items = list.data ?? []
  const needle = q.trim().toLowerCase()
  const filtered = items
    .filter((m) => level === 'all' || m.scope === level)
    .filter((m) => !needle || `${m.name} ${(m.aliases ?? []).join(' ')}`.toLowerCase().includes(needle))
  const shown = FEATURES.metricRefs && sort === 'refs' ? [...filtered].sort((a, b) => (refs.data?.get(b.id) ?? 0) - (refs.data?.get(a.id) ?? 0)) : filtered
  const paged = usePaged(shown)
  const count = (l: LevelFilter) => (l === 'all' ? items.length : items.filter((m) => m.scope === l).length)
  const reset = () => paged.setPage(1)
  const cols = FEATURES.metricRefs ? 7 : 6

  const confirmDelete = () => {
    if (!deleting) return
    const m = deleting
    del.mutate(m.id, {
      onSuccess: () => toast({ tone: 'success', title: `已删除「${m.name}」`, message: '提问时不会再使用这个口径。' }),
      onError: (e) => toast({ tone: 'danger', title: `删除「${m.name}」失败`, message: e instanceof ApiError ? describeError(e.code) : '请稍后重试。' }),
      onSettled: () => setDeleting(null),
    })
  }

  return (
    <>
      <div className="cb-filters">
        <div className="cb-search">
          <TextField
            aria-label="搜索指标名或别名"
            placeholder="搜索指标名或别名"
            icon="search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              reset()
            }}
          />
        </div>
        <Segmented
          label="指标级别"
          value={level}
          onChange={(v) => {
            setLevel(v)
            reset()
          }}
          items={[
            { id: 'all', label: <>全部 <span className="cb-num">{count('all')}</span></> },
            { id: 'platform', label: '平台级' },
            { id: 'team', label: '团队级' },
            { id: 'personal', label: '个人级' },
          ]}
        />
        <span className="cb-spacer" />
        {FEATURES.metricRefs ? (
          <Select
            ariaLabel="排序"
            value={sort}
            onChange={(v) => {
              setSort(v)
              reset()
            }}
            options={[
              { value: 'default', label: '默认排序' },
              { value: 'refs', label: '按引用次数排序' },
            ]}
          />
        ) : null}
      </div>
      <ListCard label="指标列表" pager={<Pager {...paged.pager} />}>
        <ListStatus
          loading={ds == null || list.isPending}
          error={list.error}
          onRetry={() => void list.refetch()}
          what="指标"
          count={shown.length}
          filtered={!!needle || level !== 'all'}
          query={needle ? q.trim() : undefined}
          onClear={() => {
            setQ('')
            setLevel('all')
          }}
          cols={cols}
          empty={
            <EmptyState
              icon="chart"
              title="还没有指标"
              action={
                <Button variant="primary" icon="plus" disabled={ds == null} onClick={() => ds != null && props.onAdd({ datasource_id: ds, name: '', aliases: [], description: '', sql_template: '', params: {}, scope: OWN_SCOPE[role] })}>
                  添加指标
                </Button>
              }
            >
              把「GMV」「复购率」这类口径固定下来，提问时就按统一的算法回答。指标保存时会用只读账号试跑校验。
            </EmptyState>
          }
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                <th style={{ width: '18%' }}>名称与别名</th>
                <th>口径</th>
                <th style={{ width: 120 }}>级别</th>
                {FEATURES.metricRefs ? <th style={{ width: 112 }}>近 30 天引用</th> : null}
                <th style={{ width: 132 }}>校验</th>
                <th style={{ width: 104 }}>最近校验</th>
                <th style={{ width: 188 }}><span className="cb-sr">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {paged.rows.map((m) => {
                const broken = removedReferences(m.sql_template, changes?.changes ?? [])
                const n = refs.data?.get(m.id)
                const editable = canEdit(m, role)
                return (
                  <tr key={m.id}>
                    <td title={`${m.name} ${(m.aliases ?? []).join('、')}`}>
                      <b className="cb-met-name">{m.name}</b>
                      <span className="cb-sub">{(m.aliases ?? []).join('、') || '—'}</span>
                    </td>
                    <td className="cb-m" title={m.sql_template}>{m.sql_template}</td>
                    <td>
                      <span className="cb-status">
                        <ScopeTag level={m.scope} />
                        {m.status === 'pending' ? <Badge tone="warning">待审核</Badge> : null}
                      </span>
                    </td>
                    {FEATURES.metricRefs ? <td className={n ? 'cb-r' : 'cb-r cb-faint'}>{n == null ? '—' : n.toLocaleString('zh-CN')}</td> : null}
                    <td>
                      {broken.length ? (
                        <span title={`口径引用的 ${broken.join('、')} 在最近一次同步中已被删除`}>
                          <Badge tone="danger">引用字段已删除</Badge>
                        </span>
                      ) : (
                        <Badge tone="success">通过</Badge>
                      )}
                    </td>
                    <td className="cb-r" title={m.last_validated_at}>{formatTime(m.last_validated_at)}</td>
                    <td>
                      <span className="cb-actions">
                        {m.scope === 'personal' && m.status === 'private' && role !== 'admin' ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={promote.isPending}
                            onClick={() =>
                              promote.mutate(
                                { kind: 'metric', ref_id: m.id },
                                { onSuccess: () => toast({ tone: 'success', title: `已提交「${m.name}」的提升申请`, message: '审核通过后会成为团队级指标。' }) },
                              )
                            }
                          >
                            申请提升
                          </Button>
                        ) : null}
                        {editable ? (
                          <>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                props.onEdit(m.id, { datasource_id: m.datasource_id, name: m.name, aliases: m.aliases ?? [], description: m.description, sql_template: m.sql_template, params: (m.params ?? {}) as MetricInput['params'], scope: m.scope })
                              }
                            >
                              编辑
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setDeleting(m)}>删除</Button>
                          </>
                        ) : (
                          <span className="cb-muted" title={`${SCOPE_NAME[m.scope]}指标由${m.scope === 'platform' ? '管理员' : m.scope === 'team' ? '团队负责人' : '创建者'}维护`}>只读</span>
                        )}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>
      {deleting ? (
        <ConfirmDialog title={`删除「${deleting.name}」`} confirmLabel="删除" danger busy={del.isPending} onClose={() => setDeleting(null)} onConfirm={confirmDelete}>
          删除后，提问时不会再使用这个口径，已有的回答不受影响。这个操作不能撤销。
        </ConfirmDialog>
      ) : null}
    </>
  )
}

function RequestsTab(props: { role: Role; ds: number | null; metrics: MetricView[]; promotions: ReturnType<typeof usePromotions> }) {
  const { role, promotions } = props
  const toast = useToast()
  const review = useReviewPromotion()
  const [view, setView] = useState<'pending' | 'done'>('pending')
  const [rejecting, setRejecting] = useState<PromotionView | null>(null)
  const [reason, setReason] = useState('')
  const all = promotions.data ?? []
  const todo = all.filter((p) => p.status === 'pending')
  const done = all.filter((p) => p.status !== 'pending')
  const shown = view === 'pending' ? todo : done
  const paged = usePaged(shown)
  // 只有管理员和团队负责人能审核；员工只看到自己提交的申请。
  const canReview = role !== 'member'

  const target = (p: PromotionView) => {
    const name = p.kind === 'metric' ? props.metrics.find((m) => m.id === p.ref_id)?.name : undefined
    return `${p.kind === 'metric' ? '指标' : '语义'} ${name ? `「${name}」` : `#${p.ref_id}`}`
  }
  const run = (p: PromotionView, approve: boolean, comment?: string) =>
    review.mutate(
      { id: p.id, approve, comment },
      {
        onSuccess: () => {
          toast(approve ? { tone: 'success', title: `已通过${target(p)}`, message: '它已成为平台级定义，所有人都能用。' } : { tone: 'success', title: `已驳回${target(p)}`, message: comment ? '驳回理由已记录。' : undefined })
          setRejecting(null)
          setReason('')
        },
        onError: (e) => toast({ tone: 'danger', title: '审核失败', message: e instanceof ApiError ? describeError(e.code) : '请稍后重试。' }),
      },
    )

  return (
    <>
      <div className="cb-filters">
        <Segmented
          label="申请状态"
          value={view}
          onChange={(v) => {
            setView(v)
            paged.setPage(1)
          }}
          items={[
            { id: 'pending', label: <>待审核 <span className="cb-num">{todo.length}</span></> },
            { id: 'done', label: <>已处理 <span className="cb-num">{done.length}</span></> },
          ]}
        />
      </div>
      <ListCard label="提升申请" pager={<Pager {...paged.pager} />}>
        <ListStatus
          loading={promotions.isPending}
          error={promotions.error}
          onRetry={() => void promotions.refetch()}
          what="申请"
          count={shown.length}
          empty={
            <EmptyState icon="check" title={view === 'pending' ? '没有待审核的申请' : '还没有处理过的申请'}>
              {view === 'pending' ? '员工提交指标或语义的提升申请后，会出现在这里。' : '通过或驳回的申请会记录在这里。'}
            </EmptyState>
          }
          cols={5}
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                <th style={{ width: '24%' }}>对象</th>
                <th>申请说明</th>
                <th style={{ width: 110 }}>状态</th>
                <th style={{ width: 120 }}>申请时间</th>
                <th style={{ width: 150 }}><span className="cb-sr">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {paged.rows.map((p) => (
                <tr key={p.id}>
                  <td title={target(p)}>{target(p)}</td>
                  <td title={p.comment}>{p.comment || <span className="cb-faint">—</span>}</td>
                  <td><Badge tone={REQUEST_STATUS[p.status].tone}>{REQUEST_STATUS[p.status].label}</Badge></td>
                  <td className="cb-r" title={p.created_at}>{formatTime(p.created_at)}</td>
                  <td>
                    {canReview && p.status === 'pending' ? (
                      <span className="cb-actions">
                        <Button size="sm" variant="ghost" disabled={review.isPending} onClick={() => setRejecting(p)}>驳回</Button>
                        <Button size="sm" variant="primary" disabled={review.isPending} onClick={() => run(p, true)}>通过</Button>
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>
      {rejecting ? (
        <Dialog title={`驳回${target(rejecting)}`} onClose={() => setRejecting(null)}>
          <div className="cb-field">
            <label htmlFor="reject-reason">驳回理由（选填）</label>
            <textarea id="reject-reason" className="cb-textarea" value={reason} placeholder="告诉申请人哪里需要调整" onChange={(e) => setReason(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRejecting(null)}>取消</Button>
            <Button variant="danger" disabled={review.isPending} onClick={() => run(rejecting, false, reason.trim() || undefined)}>驳回</Button>
          </DialogFooter>
        </Dialog>
      ) : null}
    </>
  )
}

function MetricForm(props: { value: MetricInput; busy: boolean; error: string | null; onCancel: () => void; onSave: (v: MetricInput) => void }) {
  const [f, setF] = useState(props.value)
  const [aliases, setAliases] = useState((props.value.aliases ?? []).join('、'))
  return (
    <div className="cb-form">
      <TextField label="名称" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <TextField label="别名" hint="用顿号分隔" value={aliases} onChange={(e) => setAliases(e.target.value)} />
      <TextField label="说明" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      <div className="cb-field">
        <label htmlFor="metric-sql">口径 SQL 模板</label>
        <textarea id="metric-sql" className="cb-textarea cb-mono" spellCheck={false} value={f.sql_template} onChange={(e) => setF({ ...f, sql_template: e.target.value })} />
        <div className="cb-field-hint">只允许单条 SELECT，保存时经 SQL 守卫试跑。</div>
      </div>
      <div className="cb-field">
        <span className="cb-field-label">级别</span>
        <div>
          <ScopeTag level={f.scope} />
        </div>
        <div className="cb-field-hint">{f.scope === 'personal' ? '只有你自己能用；可以申请提升为团队级。' : f.scope === 'team' ? '本团队成员都能用。' : '全平台都能用。'}</div>
      </div>
      {props.error ? <Callout tone="danger">{props.error}</Callout> : null}
      <DialogFooter>
        <Button variant="ghost" onClick={props.onCancel}>取消</Button>
        <Button variant="primary" disabled={props.busy || !f.name || !f.sql_template} onClick={() => props.onSave({ ...f, aliases: aliases.split(/[、,，\s]+/).filter(Boolean) })}>保存指标</Button>
      </DialogFooter>
    </div>
  )
}
