import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  testMcpAsk,
  useAvailableDatasources,
  useCreateMcpClient,
  useDeleteMcpClient,
  useMcpClient,
  useMcpClients,
  useMcpGrants,
  useMcpItems,
  useRestoreMcpItems,
  useRevokeMcpKey,
  useRotateMcpKey,
  useSaveMcpGrants,
  useSetMcpItem,
  useSetMcpStatus,
  useTables,
  useUpdateMcpClient,
} from '../api/hooks'
import type { McpClientRead, McpClientWrite, McpKeyCreated, OverlayItem, TableRead, TestAskReply } from '../api/types'
import { Badge, Button, Callout, ScopeTag, Switch, Tabs, TextField } from '../components/basic'
import { Check } from '../components/Check'
import { ConfirmDialog, Dialog, DialogFooter, Drawer, UnsavedDialog } from '../components/Dialog'
import { Icon } from '../components/Icon'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { HeaderActions } from '../components/PageHeader'
import { Select } from '../components/Select'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import {
  clientStatus,
  grantPayload,
  grantState,
  liveKeys,
  MASK_LABEL,
  sameGrants,
  setColumn,
  setTable,
  snippets,
  STATUS_LABEL,
  tableState,
  type GrantState,
  type MaskChoice,
} from '../lib/mcp'

const DEFAULTS: McpClientWrite = { name: '', expires_at: null, daily_call_limit: 2000, default_rows: 100, max_rows: 1000, ask_timeout_seconds: 60, allow_run_sql: false }
const endpoint = () => `${window.location.origin}/mcp`
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '—')
const short = (iso: string) => new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
const RESULT_LABEL: Record<string, string> = { success: '成功', empty: '无数据', clarify: '需澄清' }
const fail = (e: unknown) => (e instanceof ApiError ? describeError(e.code) : undefined)

/** 管理员：给外部 AI Agent 开的 MCP 接入。每个接入有自己的可查范围、口径和调用上限。 */
export function McpPage() {
  const clients = useMcpClients()
  const create = useCreateMcpClient()
  const setStatus = useSetMcpStatus()
  const toast = useToast()
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState<number | null>(null)
  const [revealed, setRevealed] = useState<McpKeyCreated | null>(null)
  const items = clients.data ?? []

  const toggle = (c: McpClientRead, on: boolean) =>
    setStatus.mutate(
      { id: c.id, active: on },
      {
        onSuccess: () => toast({ tone: 'success', title: `${c.name}：${on ? '已启用' : '已停用'}`, message: on ? undefined : '下一次调用起拒绝，正在进行的问数会跑完。' }),
        onError: (e) => toast({ tone: 'danger', title: `${c.name}：操作失败`, message: fail(e) }),
      },
    )

  const addButton = (
    <Button variant="primary" icon="plus" onClick={() => setCreating(true)}>
      新建接入
    </Button>
  )

  return (
    <div className="cb-page">
      <HeaderActions>{addButton}</HeaderActions>
      <Callout tone="info">
        其他 AI Agent（Claude Code、Cursor 等）通过 MCP 协议用 AskData 问数。每个接入只能查勾选的表和字段，默认使用平台级语义和指标，可以单独修改。
      </Callout>
      <ListCard label="MCP 接入列表">
        <ListStatus
          loading={clients.isPending}
          error={clients.error}
          onRetry={() => void clients.refetch()}
          what="接入"
          count={items.length}
          cols={6}
          empty={
            <EmptyState icon="plug" title="还没有 MCP 接入" action={addButton}>
              新建接入后，复制配置到 Agent 里即可连接。
            </EmptyState>
          }
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                <th style={{ width: '26%' }}>接入</th>
                <th style={{ width: 96 }}>状态</th>
                <th style={{ width: 112 }} className="cb-r">可查数据源</th>
                <th style={{ width: 140 }} className="cb-r">今日调用</th>
                <th>最近调用</th>
                <th style={{ width: 160 }}><span className="cb-sr">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => {
                const status = clientStatus(c)
                return (
                  <tr key={c.id} className={status === 'active' ? undefined : 'cb-row-off'}>
                    <td title={c.name}>
                      <span className="cb-mcp-name">{c.name}</span>
                      <span className="cb-sub cb-m">{c.public_id}</span>
                    </td>
                    <td>
                      <Badge tone={status === 'active' ? 'success' : status === 'expired' ? 'warning' : undefined}>{STATUS_LABEL[status]}</Badge>
                    </td>
                    <td className="cb-r">{(c.datasource_ids ?? []).length}</td>
                    <td className="cb-r">
                      <span className="cb-num">{c.calls_today}</span>
                      <span className="cb-faint"> / {c.daily_call_limit}</span>
                    </td>
                    <td>{c.last_used_at ? when(c.last_used_at) : <span className="cb-faint">从未调用</span>}</td>
                    <td>
                      <span className="cb-actions">
                        <Button size="sm" variant="ghost" onClick={() => setOpenId(c.id)}>
                          管理
                        </Button>
                        <Switch checked={c.status === 'active'} label={`启用 ${c.name}`} disabled={setStatus.isPending} onChange={(on) => toggle(c, on)} />
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>

      {creating ? (
        <Dialog title="新建 MCP 接入" size="lg" onClose={() => setCreating(false)}>
          <ClientForm
            value={DEFAULTS}
            busy={create.isPending}
            error={fail(create.error) ?? null}
            submitLabel="创建接入"
            onCancel={() => setCreating(false)}
            onSave={(form) =>
              create.mutate(form, {
                onSuccess: (created) => {
                  setCreating(false)
                  setRevealed(created)
                  setOpenId(created.client_id ?? null)
                },
              })
            }
          />
        </Dialog>
      ) : null}
      {openId != null ? <McpDetail id={openId} justCreated={revealed} onClose={() => setOpenId(null)} /> : null}
      {revealed ? <KeyDialog created={revealed} title="接入已创建" onClose={() => setRevealed(null)} /> : null}
    </div>
  )
}

function ClientForm(props: { value: McpClientWrite; busy: boolean; error: string | null; submitLabel: string; onCancel?: () => void; onSave: (v: McpClientWrite) => void }) {
  const [f, setF] = useState(props.value)
  const int = (v: string) => (v === '' ? 0 : Math.floor(Number(v)))
  const problems = [
    !f.name.trim() && '填写名称',
    !(f.daily_call_limit >= 1) && '每日调用上限至少 1 次',
    !(f.max_rows >= 1 && f.max_rows <= 1000) && '单次最多返回 1–1000 行',
    !(f.default_rows >= 1 && f.default_rows <= f.max_rows) && '默认返回行数不能超过单次最多返回行数',
    !(f.ask_timeout_seconds >= 10 && f.ask_timeout_seconds <= 120) && '问数超时 10–120 秒',
  ].filter(Boolean) as string[]
  const date = f.expires_at ? f.expires_at.slice(0, 10) : ''
  return (
    <>
      <div className="cb-form-grid">
        <div className="cb-full">
          <TextField label="名称" placeholder="例如：销售分析 Agent" value={f.name} maxLength={100} onChange={(e) => setF({ ...f, name: e.target.value })} />
        </div>
        <TextField label="每日调用上限（次）" type="number" min={1} value={f.daily_call_limit} hint="ask 和 run_sql 计数；列表、检索类调用不计" onChange={(e) => setF({ ...f, daily_call_limit: int(e.target.value) })} />
        <TextField label="到期日期" type="date" value={date} hint="留空表示长期有效" onChange={(e) => setF({ ...f, expires_at: e.target.value ? new Date(`${e.target.value}T23:59:59`).toISOString() : null })} />
        <TextField label="默认返回行数" type="number" min={1} max={1000} value={f.default_rows} hint="Agent 不指定 max_rows 时返回的行数" onChange={(e) => setF({ ...f, default_rows: int(e.target.value) })} />
        <TextField label="单次最多返回行数" type="number" min={1} max={1000} value={f.max_rows} hint="另有 3 万字符上限，宽表会更早截断" onChange={(e) => setF({ ...f, max_rows: int(e.target.value) })} />
        <TextField label="问数超时（秒）" type="number" min={10} max={120} value={f.ask_timeout_seconds} onChange={(e) => setF({ ...f, ask_timeout_seconds: int(e.target.value) })} />
        <label className="cb-radio cb-full">
          <input type="checkbox" checked={f.allow_run_sql} onChange={(e) => setF({ ...f, allow_run_sql: e.target.checked })} />
          允许 Agent 直接执行 SQL（run_sql）
        </label>
        {f.allow_run_sql ? (
          <div className="cb-full">
            <Callout tone="warning">开启后，Agent 可以自己写 SQL，不经过平台的语义和指标口径。仍然只能查授权的字段，脱敏和安全检查照常生效。</Callout>
          </div>
        ) : null}
      </div>
      {problems.length ? <div className="cb-field-hint">{problems[0]}</div> : null}
      {props.error ? <Callout tone="danger">{props.error}</Callout> : null}
      <DialogFooter>
        {props.onCancel ? <Button onClick={props.onCancel}>取消</Button> : null}
        <Button variant="primary" disabled={props.busy || problems.length > 0} onClick={() => props.onSave({ ...f, name: f.name.trim() })}>
          {props.submitLabel}
        </Button>
      </DialogFooter>
    </>
  )
}

function CopyButton(props: { text: string; label?: string }) {
  const toast = useToast()
  return (
    <Button
      size="sm"
      icon="copy"
      onClick={() =>
        void navigator.clipboard.writeText(props.text).then(
          () => toast({ tone: 'success', title: '已复制' }),
          () => toast({ tone: 'danger', title: '复制失败', message: '请手动选中复制。' }),
        )
      }
    >
      {props.label ?? '复制'}
    </Button>
  )
}

/** 完整密钥只显示这一次。 */
function KeyDialog(props: { created: McpKeyCreated; title: string; onClose: () => void }) {
  const [tab, setTab] = useState('claude')
  const list = snippets(endpoint(), props.created.key)
  const shown = list.find((s) => s.id === tab) ?? list[0]
  return (
    <Dialog title={props.title} size="lg" onClose={props.onClose}>
      <Callout tone="warning">这是完整的 API Key，关闭后不再显示。请现在复制并妥善保存；遗失后只能轮换出新的 Key。</Callout>
      <div className="cb-mcp-key">
        <code className="cb-m">{props.created.key}</code>
        <CopyButton text={props.created.key} label="复制 Key" />
      </div>
      <Tabs label="接入配置" value={tab} onChange={setTab} items={list.map((s) => ({ id: s.id, label: s.label }))} />
      {shown ? <Snippet text={shown.text} /> : null}
      <DialogFooter>
        <Button variant="primary" onClick={props.onClose}>
          我已保存
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

function Snippet(props: { text: string }) {
  return (
    <div className="cb-mcp-snippet">
      <pre className="cb-m">{props.text}</pre>
      <CopyButton text={props.text} />
    </div>
  )
}

type TabId = 'basic' | 'grants' | 'items' | 'test' | 'connect'

function McpDetail(props: { id: number; justCreated: McpKeyCreated | null; onClose: () => void }) {
  const client = useMcpClient(props.id)
  const [tab, setTab] = useState<TabId>(props.justCreated ? 'grants' : 'basic')
  const [dirty, setDirty] = useState(false)
  const [pending, setPending] = useState<TabId | 'close' | null>(null)
  const c = client.data
  const go = (next: TabId | 'close') => {
    if (dirty) setPending(next)
    else if (next === 'close') props.onClose()
    else setTab(next)
  }
  const status = c ? clientStatus(c) : 'active'
  return (
    <Drawer
      wide
      title={
        <span className="cb-mcp-title">
          {c?.name ?? 'MCP 接入'}
          {c ? <Badge tone={status === 'active' ? 'success' : status === 'expired' ? 'warning' : undefined}>{STATUS_LABEL[status]}</Badge> : null}
        </span>
      }
      onClose={() => go('close')}
    >
      {!c ? (
        <ListStatus loading={client.isPending} error={client.error} onRetry={() => void client.refetch()} what="接入" count={0} empty={<EmptyState icon="plug" title="接入不存在">它可能已被删除。</EmptyState>}>
          {null}
        </ListStatus>
      ) : (
        <>
          <Tabs
            label="接入设置"
            value={tab}
            onChange={(id) => go(id as TabId)}
            items={[
              { id: 'basic', label: '基本' },
              { id: 'grants', label: '数据权限', count: (c.datasource_ids ?? []).length },
              { id: 'items', label: '语义与指标', count: c.override_count || undefined },
              { id: 'test', label: '测试问数' },
              { id: 'connect', label: '接入方式' },
            ]}
          />
          <div className="cb-mcp-tab">
            {tab === 'basic' ? <BasicTab client={c} onDeleted={props.onClose} /> : null}
            {tab === 'grants' ? <GrantsTab client={c} onDirty={setDirty} /> : null}
            {tab === 'items' ? <ItemsTab client={c} /> : null}
            {tab === 'test' ? <TestTab client={c} /> : null}
            {tab === 'connect' ? <ConnectTab client={c} /> : null}
          </div>
        </>
      )}
      {pending ? (
        <UnsavedDialog
          canSave={false}
          onStay={() => setPending(null)}
          onDiscard={() => {
            setDirty(false)
            const next = pending
            setPending(null)
            if (next === 'close') props.onClose()
            else setTab(next)
          }}
        />
      ) : null}
    </Drawer>
  )
}

function BasicTab(props: { client: McpClientRead; onDeleted: () => void }) {
  const c = props.client
  const update = useUpdateMcpClient()
  const remove = useDeleteMcpClient()
  const toast = useToast()
  const [confirm, setConfirm] = useState(false)
  const value: McpClientWrite = { name: c.name, expires_at: c.expires_at ?? null, daily_call_limit: c.daily_call_limit, default_rows: c.default_rows, max_rows: c.max_rows, ask_timeout_seconds: c.ask_timeout_seconds, allow_run_sql: c.allow_run_sql }
  return (
    <>
      <ClientForm
        key={c.id}
        value={value}
        busy={update.isPending}
        error={fail(update.error) ?? null}
        submitLabel="保存设置"
        onSave={(body) => update.mutate({ id: c.id, body }, { onSuccess: () => toast({ tone: 'success', title: '设置已保存' }) })}
      />
      <section className="cb-mcp-danger" aria-label="删除接入">
        <div>
          <strong>删除接入</strong>
          <div className="cb-sub">吊销全部 Key，清空授权、口径修改和会话。查询日志与审计记录保留。</div>
        </div>
        <Button variant="danger" icon="trash" onClick={() => setConfirm(true)}>
          删除接入
        </Button>
      </section>
      {confirm ? (
        <ConfirmDialog
          title={`删除「${c.name}」`}
          confirmLabel="删除接入"
          danger
          busy={remove.isPending}
          onClose={() => setConfirm(false)}
          onConfirm={() =>
            remove.mutate(c.id, {
              onSuccess: () => {
                toast({ tone: 'success', title: `${c.name}：已删除` })
                props.onDeleted()
              },
              onError: (e) => toast({ tone: 'danger', title: '删除失败', message: fail(e) }),
            })
          }
        >
          删除后无法恢复：使用这个接入的 Agent 会立即无法连接。如果只是想暂时停用，请在列表里关闭开关。
        </ConfirmDialog>
      ) : null}
    </>
  )
}

function DatasourceSelect(props: { value: number | null; onChange: (id: number) => void }) {
  const sources = useAvailableDatasources()
  const items = sources.data?.items ?? []
  const value = props.value ?? items[0]?.id ?? null
  if (props.value == null && value != null) queueMicrotask(() => props.onChange(value))
  return <Select ariaLabel="数据源" value={value} onChange={(id) => id != null && props.onChange(id)} options={items.map((d) => ({ value: d.id, label: `${d.type} · ${d.name}` }))} />
}

function GrantsTab(props: { client: McpClientRead; onDirty: (dirty: boolean) => void }) {
  const c = props.client
  const toast = useToast()
  const [ds, setDs] = useState<number | null>(null)
  const tables = useTables(ds)
  const grants = useMcpGrants(c.id, ds)
  const save = useSaveMcpGrants(c.id, ds)
  const base = useMemo(() => grantState(grants.data), [grants.data])
  const key = `${ds}:${grants.dataUpdatedAt}`
  const [draft, setDraft] = useState<{ key: string; state: GrantState } | null>(null)
  const state = draft?.key === key ? draft.state : base
  const dirty = !sameGrants(state, base)
  const [open, setOpen] = useState<Set<number>>(new Set())
  const [q, setQ] = useState('')
  const edit = (next: GrantState) => {
    setDraft({ key, state: next })
    props.onDirty(!sameGrants(next, base))
  }
  const persist = () =>
    save.mutate(grantPayload(state), {
      onSuccess: () => {
        setDraft(null)
        props.onDirty(false)
        toast({ tone: 'success', title: '数据权限已保存', message: '下一次调用起生效。' })
      },
      onError: (e) => toast({ tone: 'danger', title: '保存失败', message: fail(e) }),
    })
  const toggleOpen = (id: number) =>
    setOpen((cur) => {
      const next = new Set(cur)
      if (!next.delete(id)) next.add(id)
      return next
    })
  const needle = q.trim().toLowerCase()
  const rows = (tables.data ?? [])
    .map((t: TableRead) => {
      const nameHit = !needle || t.name.toLowerCase().includes(needle)
      const cols = nameHit ? t.columns ?? [] : (t.columns ?? []).filter((col) => col.name.toLowerCase().includes(needle))
      return nameHit || cols.length ? { t, cols, forced: !nameHit } : null
    })
    .filter((r): r is { t: TableRead; cols: NonNullable<TableRead['columns']>; forced: boolean } => r != null)
  const granted = Object.keys(state).length
  return (
    <>
      {c.stale_grants ? <Callout tone="warning">{c.stale_grants} 个已授权字段在数据源同步后已不存在，已自动失效，无需处理。</Callout> : null}
      <div className="cb-filters">
        <DatasourceSelect value={ds} onChange={(id) => (dirty ? toast({ tone: 'warning', title: '先保存或撤销当前修改' }) : setDs(id))} />
        <div className="cb-search">
          <TextField aria-label="筛选表或字段" placeholder="按表名或字段名筛选" icon="search" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <span className="cb-faint">已授权 {granted} 个字段</span>
        {dirty ? <Badge tone="warning">有未保存的修改</Badge> : null}
        <Button disabled={!dirty} onClick={() => edit(base)}>
          撤销修改
        </Button>
        <Button variant="primary" disabled={!dirty || save.isPending} onClick={persist}>
          保存
        </Button>
      </div>
      <section className="cb-panel cb-pm-tree cb-mcp-tree" aria-label="可查的表和字段">
        <div className="cb-pm-head" role="row">
          <span>表 / 字段名</span>
          <span>类型</span>
          <span>脱敏</span>
          <span>可查</span>
        </div>
        <ListStatus
          loading={tables.isLoading || grants.isLoading}
          error={tables.error ?? grants.error}
          onRetry={() => {
            void tables.refetch()
            void grants.refetch()
          }}
          what="表"
          count={rows.length}
          filtered={!!needle}
          query={needle ? q.trim() : undefined}
          onClear={() => setQ('')}
          empty={<EmptyState icon="database" title="这个数据源还没有表">先在「数据源」里同步表结构。</EmptyState>}
        >
          {rows.map(({ t, cols, forced }) => {
            const expanded = forced || open.has(t.id)
            const count = (t.columns ?? []).filter((col) => col.id in state).length
            return (
              <div key={t.id} className="cb-pm-table">
                <div className="cb-pm-row">
                  <button type="button" className="cb-pm-toggle" aria-expanded={expanded} onClick={() => toggleOpen(t.id)}>
                    <Icon name="chevronDown" size="sm" />
                    <span className="cb-m cb-pm-name">{t.name}</span>
                    <Badge>{count ? `${count}/${(t.columns ?? []).length}` : `${(t.columns ?? []).length} 个字段`}</Badge>
                  </button>
                  <span />
                  <span />
                  <Check state={tableState(t, state)} label={`可查表 ${t.name}`} onChange={(on) => edit(setTable(state, t, on))} />
                </div>
                {expanded
                  ? cols.map((col) => {
                      const mask = state[col.id]
                      return (
                        <div key={col.id} className="cb-pm-row cb-pm-col">
                          <span className="cb-m">{col.name}</span>
                          <span>
                            <Badge mono>{col.data_type}</Badge>
                          </span>
                          {mask ? (
                            <Select
                              ariaLabel={`${t.name}.${col.name} 脱敏方式`}
                              size="sm"
                              value={mask}
                              onChange={(m) => edit({ ...state, [col.id]: m as MaskChoice })}
                              options={(Object.keys(MASK_LABEL) as MaskChoice[]).map((m) => ({ value: m, label: MASK_LABEL[m] }))}
                            />
                          ) : (
                            <span className="cb-faint">—</span>
                          )}
                          <Check state={mask ? 'on' : 'off'} label={`可查字段 ${t.name}.${col.name}`} onChange={(on) => edit(setColumn(state, col.id, on))} />
                        </div>
                      )
                    })
                  : null}
              </div>
            )
          })}
        </ListStatus>
      </section>
    </>
  )
}

const ITEM_FIELDS = {
  semantic: [
    { key: 'aliases', label: '别名', hint: '用逗号分隔' },
    { key: 'description', label: '说明' },
  ],
  metric: [
    { key: 'aliases', label: '别名', hint: '用逗号分隔' },
    { key: 'description', label: '说明' },
    { key: 'sql_template', label: 'SQL 定义', hint: '只能使用这个接入可查的字段' },
  ],
} as const

function ItemsTab(props: { client: McpClientRead }) {
  const c = props.client
  const toast = useToast()
  const [ds, setDs] = useState<number | null>(null)
  const items = useMcpItems(c.id, ds)
  const setItem = useSetMcpItem(c.id, ds)
  const restore = useRestoreMcpItems(c.id)
  const [editing, setEditing] = useState<OverlayItem | null>(null)
  const list = items.data ?? []
  const changed = list.filter((i) => i.state !== 'default').length
  const act = (item: OverlayItem, action: 'hide' | 'restore') => {
    const done = { onSuccess: () => toast({ tone: 'success', title: `${item.name}：${action === 'hide' ? '已对这个接入隐藏' : '已恢复平台默认'}` }), onError: (e: unknown) => toast({ tone: 'danger', title: '操作失败', message: fail(e) }) }
    if (action === 'hide') setItem.mutate({ kind: item.kind, item_id: item.item_id, action: 'hide', payload: {} }, done)
    else restore.mutate({ kind: item.kind, item_id: item.item_id }, done)
  }
  return (
    <>
      <Callout tone="info">默认使用平台级的语义和指标。在这里修改或隐藏只影响这个接入，平台上的定义不变；恢复默认即撤销修改。</Callout>
      <div className="cb-filters">
        <DatasourceSelect value={ds} onChange={setDs} />
        <span className="cb-faint">
          {changed ? `${changed} 项已修改或隐藏` : '全部为平台默认'}
          {(c.override_count ?? 0) > changed ? `；另有 ${(c.override_count ?? 0) - changed} 项修改不在此列（属于其他数据源，或用到的字段未授权），授权恢复后自动生效` : ''}
        </span>
        <Button disabled={!c.override_count || restore.isPending} onClick={() => restore.mutate({}, { onSuccess: (r) => toast({ tone: 'success', title: `已恢复 ${r.restored} 项为平台默认` }) })}>
          全部恢复默认
        </Button>
      </div>
      <ListCard label="语义与指标">
        <ListStatus
          loading={items.isLoading}
          error={items.error}
          onRetry={() => void items.refetch()}
          what="语义与指标"
          count={list.length}
          cols={4}
          empty={<EmptyState icon="book" title="没有可继承的语义和指标">平台级语义和指标只有用到的字段都已授权给这个接入时才会出现。</EmptyState>}
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                <th>名称 · 当前说明</th>
                <th style={{ width: 88 }}>状态</th>
                <th style={{ width: 184 }}><span className="cb-sr">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {list.map((item) => (
                <tr key={`${item.kind}:${item.item_id}`} className={item.state === 'hidden' ? 'cb-row-off' : undefined}>
                  <td title={String(item.effective.description ?? '')}>
                    <span className="cb-mcp-item">
                      <span className="cb-mcp-name">{item.name}</span>
                      <Badge>{item.kind === 'metric' ? '指标' : '语义'}</Badge>
                      <ScopeTag level="platform" />
                    </span>
                    <span className="cb-sub cb-mcp-def">{String(item.effective.description ?? '') || '无说明'}</span>
                  </td>
                  <td>
                    <Badge tone={item.state === 'edited' ? 'info' : undefined}>{item.state === 'edited' ? '已修改' : item.state === 'hidden' ? '已隐藏' : '平台默认'}</Badge>
                  </td>
                  <td>
                    <span className="cb-actions">
                      {item.state === 'hidden' ? null : (
                        <Button size="sm" variant="ghost" onClick={() => setEditing(item)}>
                          修改
                        </Button>
                      )}
                      {item.state === 'hidden' ? null : (
                        <Button size="sm" variant="ghost" onClick={() => act(item, 'hide')}>
                          隐藏
                        </Button>
                      )}
                      {item.state === 'default' ? null : (
                        <Button size="sm" variant="ghost" onClick={() => act(item, 'restore')}>
                          恢复默认
                        </Button>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>
      {editing ? (
        <ItemEditor
          item={editing}
          busy={setItem.isPending}
          error={fail(setItem.error) ?? null}
          onClose={() => setEditing(null)}
          onSave={(payload) =>
            setItem.mutate(
              { kind: editing.kind, item_id: editing.item_id, action: 'edit', payload },
              {
                onSuccess: () => {
                  toast({ tone: 'success', title: `${editing.name}：已为这个接入修改` })
                  setEditing(null)
                },
              },
            )
          }
        />
      ) : null}
    </>
  )
}

function ItemEditor(props: { item: OverlayItem; busy: boolean; error: string | null; onClose: () => void; onSave: (payload: Record<string, unknown>) => void }) {
  const fields = ITEM_FIELDS[props.item.kind]
  const text = (v: unknown) => (Array.isArray(v) ? v.join('，') : String(v ?? ''))
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f.key, text(props.item.effective[f.key])])))
  const payload = () => {
    const out: Record<string, unknown> = {}
    for (const f of fields) {
      const raw = values[f.key] ?? ''
      const value = f.key === 'aliases' ? raw.split(/[,，]/).map((s) => s.trim()).filter(Boolean) : raw
      if (JSON.stringify(value) !== JSON.stringify(props.item.platform[f.key] ?? (f.key === 'aliases' ? [] : ''))) out[f.key] = value
    }
    return out
  }
  const changes = payload()
  return (
    <Dialog title={`修改「${props.item.name}」`} size="lg" onClose={props.onClose}>
      <Callout tone="info">只对这个接入生效。平台定义以后再修改，这里的版本保持不变，可以随时恢复默认。</Callout>
      <div className="cb-form-grid">
        {fields.map((f) => (
          <div key={f.key} className="cb-full cb-field">
            <label htmlFor={`mcp-item-${f.key}`}>{f.label}</label>
            {f.key === 'sql_template' || f.key === 'description' ? (
              <textarea id={`mcp-item-${f.key}`} className={f.key === 'sql_template' ? 'cb-textarea cb-m' : 'cb-textarea'} rows={f.key === 'sql_template' ? 6 : 3} value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
            ) : (
              <input id={`mcp-item-${f.key}`} className="cb-input" value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
            )}
            <div className="cb-field-hint">
              {'hint' in f ? `${f.hint}。` : ''}平台：{text(props.item.platform[f.key]) || '（空）'}
            </div>
          </div>
        ))}
      </div>
      {props.error ? <Callout tone="danger">{props.error}</Callout> : null}
      <DialogFooter>
        <Button onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={props.busy || Object.keys(changes).length === 0} onClick={() => props.onSave(changes)}>
          保存修改
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

function TestTab(props: { client: McpClientRead }) {
  const c = props.client
  const [ds, setDs] = useState<number | null>(null)
  const [question, setQuestion] = useState('')
  const [followUp, setFollowUp] = useState(true)
  const [busy, setBusy] = useState(false)
  const [reply, setReply] = useState<TestAskReply | null>(null)
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()
  const conversation = reply && !reply.is_error ? (reply.agent.conversation_id as string | undefined) : undefined
  const run = async () => {
    if (ds == null || !question.trim()) return
    setBusy(true)
    setError(null)
    try {
      setReply(await testMcpAsk(c.id, { question: question.trim(), datasource_id: ds, conversation_id: followUp ? conversation ?? null : null }))
      // A test counts against today's calls.
      void qc.invalidateQueries({ queryKey: ['mcp'] })
    } catch (e) {
      setError(fail(e) ?? '测试失败')
    } finally {
      setBusy(false)
    }
  }
  const d = reply?.diagnostics
  return (
    <>
      <Callout tone="info">以这个接入的身份真实问一次：同样的可查范围、口径修改和返回上限，计入今日调用次数，查询日志里记为「MCP 测试」。</Callout>
      <div className="cb-filters">
        <DatasourceSelect
          value={ds}
          onChange={(id) => {
            setDs(id)
            setReply(null)
          }}
        />
        {conversation ? (
          <label className="cb-pm-only">
            <input type="checkbox" checked={followUp} onChange={(e) => setFollowUp(e.target.checked)} />
            在上一轮基础上追问
          </label>
        ) : null}
      </div>
      <div className="cb-mcp-ask">
        <TextField aria-label="测试问题" placeholder="例如：上个月的销售额是多少" value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void run()} />
        <Button variant="primary" icon="play" disabled={busy || ds == null || !question.trim()} onClick={() => void run()}>
          {busy ? '问数中' : '测试问数'}
        </Button>
      </div>
      {error ? <Callout tone="danger">{error}</Callout> : null}
      {reply && d ? (
        <div className="cb-mcp-result">
          <section className="cb-panel" aria-label="诊断">
            <div className="cb-panel-head">诊断（仅管理员可见）</div>
            <dl className="cb-kv cb-mcp-kv">
              <dt>身份</dt>
              <dd>{d.principal}</dd>
              <dt>结果</dt>
              <dd>
                {reply.is_error ? <Badge tone="danger" icon="x">失败 · {String(reply.agent.code)}</Badge> : <Badge tone="success" icon="check">{RESULT_LABEL[String(reply.agent.status)] ?? String(reply.agent.status)}</Badge>}
              </dd>
              <dt>可查表</dt>
              <dd className="cb-m">{d.tables?.length ? d.tables.join('、') : '无'}</dd>
              <dt>检索到</dt>
              <dd className="cb-m">{Object.keys(d.columns ?? {}).join('、') || '—'}</dd>
              <dt>语义指标</dt>
              <dd>{d.items?.length ? d.items.map((i) => String(i.name)).join('、') : '无'}</dd>
              <dt>安全检查</dt>
              <dd>
                {d.guard === 'passed' ? '通过' : d.guard === 'blocked' ? `拦截${d.rule ? ` · ${d.rule}` : ''}` : '未到达'}
                {d.stage && reply.is_error ? <span className="cb-faint"> · 停在 {d.stage}</span> : null}
              </dd>
              <dt>脱敏字段</dt>
              <dd className="cb-m">{d.masked?.length ? d.masked.join('、') : '无'}</dd>
              <dt>Token</dt>
              <dd>
                {d.input_tokens || d.output_tokens ? `${d.input_tokens} 输入 · ${d.output_tokens} 输出` : '未调用模型'}
              </dd>
              <dt>耗时</dt>
              <dd>{Object.values(d.timings ?? {}).reduce((a, b) => a + b, 0)} ms</dd>
              <dt>日志</dt>
              <dd className="cb-m">{String(reply.agent.request_id ?? '')}{d.query_run_id ? ` · #${d.query_run_id}` : ''}</dd>
            </dl>
          </section>
          <section className="cb-panel" aria-label="Agent 收到的返回">
            <div className="cb-panel-head">Agent 收到的返回{reply.is_error ? '（is_error）' : ''}</div>
            <pre className="cb-m cb-mcp-json">{JSON.stringify(reply.agent, null, 2)}</pre>
          </section>
        </div>
      ) : null}
    </>
  )
}

function ConnectTab(props: { client: McpClientRead }) {
  const c = props.client
  const rotate = useRotateMcpKey()
  const revoke = useRevokeMcpKey()
  const toast = useToast()
  const [tab, setTab] = useState('claude')
  const [grace, setGrace] = useState(0)
  const [rotating, setRotating] = useState(false)
  const [created, setCreated] = useState<McpKeyCreated | null>(null)
  const list = snippets(endpoint(), null)
  const shown = list.find((s) => s.id === tab) ?? list[0]
  const live = new Set(liveKeys(c).map((k) => k.id))
  return (
    <>
      <div className="cb-mcp-endpoint">
        <span className="cb-faint">MCP 地址</span>
        <code className="cb-m">{endpoint()}</code>
        <CopyButton text={endpoint()} />
      </div>
      <Tabs label="接入配置" value={tab} onChange={setTab} items={list.map((s) => ({ id: s.id, label: s.label }))} />
      {shown ? <Snippet text={shown.text} /> : null}
      <ListCard
        label="API Key"
        title="API Key"
        actions={
          <Button icon="refresh" onClick={() => setRotating(true)}>
            轮换 Key
          </Button>
        }
      >
        <table className="cb-data cb-data-fixed">
          <thead>
            <tr>
              <th style={{ width: 128 }}>前缀</th>
              <th>最近使用</th>
              <th style={{ width: 168 }}>状态</th>
              <th style={{ width: 88 }}><span className="cb-sr">操作</span></th>
            </tr>
          </thead>
          <tbody>
            {[...(c.keys ?? [])].reverse().map((k) => (
              <tr key={k.id} className={live.has(k.id) ? undefined : 'cb-row-off'}>
                <td className="cb-m" title={`创建于 ${when(k.created_at)}`}>{k.prefix}…</td>
                <td>{k.last_used_at ? short(k.last_used_at) : <span className="cb-faint">未使用</span>}</td>
                <td>
                  {k.revoked_at ? <Badge>已吊销</Badge> : !live.has(k.id) ? <Badge>已过期</Badge> : k.expires_at ? <Badge tone="warning">{short(k.expires_at)} 失效</Badge> : <Badge tone="success">有效</Badge>}
                </td>
                <td>
                  {live.has(k.id) ? (
                    <Button size="sm" variant="ghost" disabled={revoke.isPending} onClick={() => revoke.mutate({ id: c.id, keyId: k.id }, { onSuccess: () => toast({ tone: 'success', title: `${k.prefix}…：已吊销` }) })}>
                      吊销
                    </Button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ListCard>
      {rotating ? (
        <Dialog title="轮换 API Key" onClose={() => setRotating(false)}>
          <div>生成一个新 Key。旧 Key 在下面的时间后失效，给 Agent 留出换配置的时间。</div>
          <div className="cb-field">
            <label htmlFor="mcp-grace">旧 Key 失效</label>
            <Select id="mcp-grace" value={grace} onChange={(v) => setGrace(Number(v))} options={[{ value: 0, label: '立即（Key 泄露时选这个）' }, { value: 1, label: '1 小时后' }, { value: 24, label: '24 小时后' }]} />
          </div>
          <DialogFooter>
            <Button onClick={() => setRotating(false)}>取消</Button>
            <Button
              variant="primary"
              disabled={rotate.isPending}
              onClick={() =>
                rotate.mutate(
                  { id: c.id, grace_hours: grace },
                  {
                    onSuccess: (k) => {
                      setRotating(false)
                      setCreated(k)
                    },
                    onError: (e) => toast({ tone: 'danger', title: '轮换失败', message: fail(e) }),
                  },
                )
              }
            >
              生成新 Key
            </Button>
          </DialogFooter>
        </Dialog>
      ) : null}
      {created ? <KeyDialog created={created} title="新的 API Key" onClose={() => setCreated(null)} /> : null}
    </>
  )
}
