import { StatCard, Stats } from '../components/StatCard'
import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueries, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { runSync, testDatasource, testDatasourceDraft, useDatasources, useMetrics, useSaveDatasource, useSemantics, useSetDatasourceStatus } from '../api/hooks'
import type { DatasourceRead, DatasourceWrite, JobView, TableRead } from '../api/types'
import { Badge, Button, Callout, Segmented, Switch, TextField } from '../components/basic'
import { DatasourceDetail } from '../components/DatasourceDetail'
import { Dialog, DialogFooter, Drawer } from '../components/Dialog'
import { HeaderActions } from '../components/PageHeader'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { Pager, usePaged } from '../components/Pager'
import { UploadDialog } from '../components/UploadDialog'
import { Select } from '../components/Select'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { formatTime } from '../lib/format'
import { CHANGE_LABEL, diffTables, useSchemaChanges, type ChangeSet } from '../state/schemaChanges'

type Kind = 'mysql' | 'postgresql' | 'kingbase' | 'gaussdb' | 'clickhouse'

export const KINDS: { kind: Kind; label: string; icon: string; port?: number; hint?: string }[] = [
  { kind: 'mysql', label: 'MySQL', icon: '/datasource-icons/icon_mysql.png', port: 3306 },
  { kind: 'postgresql', label: 'PostgreSQL', icon: '/datasource-icons/icon_PostgreSQL.png', port: 5432 },
  { kind: 'kingbase', label: '人大金仓', icon: '/datasource-icons/icon_kingbase.svg', port: 54321 },
  { kind: 'gaussdb', label: 'GaussDB', icon: '/datasource-icons/icon_gaussdb.svg', port: 8000, hint: '也支持 openGauss，默认端口 5432' },
  { kind: 'clickhouse', label: 'ClickHouse', icon: '/datasource-icons/icon_clickhouse.svg', port: 8123, hint: '使用 HTTP 端口' },
]

/** 上传的 Excel / CSV 存在平台内置库里，不需要连接信息。 */
const UPLOAD: { kind: string; label: string; icon: string; port?: number; hint?: string } = { kind: 'upload', label: '上传文件', icon: '/datasource-icons/icon_excel.png' }

export const kindMeta = (kind: string) => (kind === 'upload' ? UPLOAD : KINDS.find((k) => k.kind === kind))

const blank = (kind: Kind): DatasourceWrite => ({
  name: '',
  type: kind,
  host: '',
  port: kindMeta(kind)?.port ?? 5432,
  database: '',
  schema_name: (kind === 'postgresql' || kind === 'kingbase' || kind === 'gaussdb') ? 'public' : 'default',
  username: '',
  password: '',
  tls: false,
})

export function KindTag({ kind }: { kind: string }) {
  const meta = kindMeta(kind)
  return (
    <span className="cb-kindtag">
      {meta ? <img src={meta.icon} alt="" /> : null}
      {meta?.label ?? kind}
    </span>
  )
}

type Dialogs =
  | { kind: 'pick' }
  | { kind: 'form'; id?: number; form: DatasourceWrite }
  | { kind: 'detail'; datasource: DatasourceRead }
  | { kind: 'upload'; datasource?: DatasourceRead }
  | { kind: 'history'; datasource: DatasourceRead; jobs: JobView[] }
  | { kind: 'changes'; datasource: DatasourceRead }
  | null

type Filter = 'all' | 'db' | 'upload'

export function DatasourcesPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const list = useDatasources()
  const status = useSetDatasourceStatus()
  const changes = useSchemaChanges((s) => s.byDatasource)
  const record = useSchemaChanges((s) => s.record)
  const [dialog, setDialog] = useState<Dialogs>(null)
  const [syncing, setSyncing] = useState<Set<number>>(new Set())
  const [filter, setFilter] = useState<Filter>('all')
  const [q, setQ] = useState('')
  const items = list.data?.items ?? []
  // 每个数据源的表（数表数、最近同步时间）和上传历史（文件数），与其他页面共用缓存。
  const tables = useQueries({ queries: items.map((d) => ({ queryKey: ['tables', d.id], queryFn: () => api<TableRead[]>('GET', `/api/schema/${d.id}/tables`), staleTime: 60_000 })) })
  const uploads = useQueries({
    queries: items.map((d) => ({ queryKey: ['upload-history', d.id], queryFn: () => api<JobView[]>('GET', `/api/upload/history/${d.id}`), enabled: d.type === 'upload', staleTime: 60_000 })),
  })
  const tablesOf = (i: number) => tables[i]?.data
  const lastSync = (ts: TableRead[] | undefined) => ts?.reduce<string | null>((max, t) => (t.synced_at && (!max || t.synced_at > max) ? t.synced_at : max), null) ?? null
  const changeCount = (id: number) => changes[id]?.changes.length ?? 0
  const needsWork = items.filter((d) => changeCount(d.id) > 0 || (d.type !== 'upload' && !d.read_only_account)).length

  const needle = q.trim().toLowerCase()
  const shown = items
    .map((d, i) => ({ d, i }))
    .filter(({ d }) => (filter === 'all' ? true : filter === 'upload' ? d.type === 'upload' : d.type !== 'upload'))
    .filter(({ d }) => !needle || d.name.toLowerCase().includes(needle) || `${d.host}:${d.port}/${d.database}`.toLowerCase().includes(needle))
  const paged = usePaged(shown)
  const dbCount = items.filter((d) => d.type !== 'upload').length

  const test = async (d: DatasourceRead) => {
    try {
      const r = await testDatasource(d.id)
      toast(
        r.available
          ? { tone: r.warning ? 'warning' : 'success', title: `${d.name}：连接正常`, message: r.warning ?? undefined }
          : { tone: 'danger', title: `${d.name}：连接失败`, message: '请检查地址、账号和网络后重试。' },
      )
    } catch (e) {
      toast({ tone: 'danger', title: `${d.name}：连接失败`, message: e instanceof ApiError ? describeError(e.code) : undefined })
    }
  }

  const sync = async (d: DatasourceRead) => {
    setSyncing((s) => new Set(s).add(d.id))
    toast({ tone: 'info', title: `${d.name}：正在同步`, message: '同步完成后会提示结果。' })
    try {
      // 同步前后各取一次表结构，对比出新增和删除。
      const before = await qc.fetchQuery({ queryKey: ['tables', d.id], queryFn: () => api<TableRead[]>('GET', `/api/schema/${d.id}/tables`), staleTime: 0 })
      const r = await runSync(d.id)
      const after = await qc.fetchQuery({ queryKey: ['tables', d.id], queryFn: () => api<TableRead[]>('GET', `/api/schema/${d.id}/tables`), staleTime: 0 })
      const found = r.status === 'success' ? diffTables(before, after) : []
      record(d.id, found)
      toast(
        r.status !== 'success'
          ? { tone: 'danger', title: `${d.name}：同步失败`, message: r.error ? describeError(r.error) : '请稍后重试。' }
          : found.length
            ? { tone: 'warning', title: `${d.name}：同步完成，发现 ${found.length} 处字段变化`, message: '新增的表和字段默认对团队和员工可见。', action: { label: '查看', onClick: () => setDialog({ kind: 'changes', datasource: d }) } }
            : { tone: 'success', title: `${d.name}：同步完成`, message: '表结构没有变化。' },
      )
      void qc.invalidateQueries({ queryKey: ['grants', d.id] })
      void qc.invalidateQueries({ queryKey: ['scope', d.id] })
    } catch (e) {
      toast({ tone: 'danger', title: `${d.name}：同步失败`, message: e instanceof ApiError ? describeError(e.code) : undefined })
    } finally {
      setSyncing((s) => {
        const next = new Set(s)
        next.delete(d.id)
        return next
      })
    }
  }

  const uploaded = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['datasources'] })
    void qc.invalidateQueries({ queryKey: ['tables'] })
    void qc.invalidateQueries({ queryKey: ['upload-history'] })
  }, [qc])

  const edit = (d: DatasourceRead) =>
    setDialog({
      kind: 'form',
      id: d.id,
      form: { name: d.name, type: d.type as DatasourceWrite['type'], host: d.host, port: d.port, database: d.database, schema_name: d.schema_name, username: d.username, password: '', tls: d.tls },
    })

  const statusOf = (d: DatasourceRead) => {
    const n = changeCount(d.id)
    return (
      <span className="cb-status">
        {d.status === 'pending' ? <Badge tone="info">导入中</Badge> : d.status === 'disabled' ? <Badge>已停用</Badge> : n === 0 && (d.type === 'upload' || d.read_only_account) ? <Badge tone="success">正常</Badge> : null}
        {n ? (
          <button type="button" className="cb-badge cb-badge-warning" onClick={() => setDialog({ kind: 'changes', datasource: d })}>
            字段变化 {n}
          </button>
        ) : null}
        {d.type !== 'upload' && !d.read_only_account && d.status !== 'disabled' ? (
          <span className="cb-badge cb-badge-warning" title={d.warning ?? '连接账号有写权限，建议换成只读账号'}>
            非只读账号
          </span>
        ) : null}
      </span>
    )
  }

  return (
    <div className="cb-page">
      <HeaderActions>
        <Button variant="primary" icon="plus" onClick={() => setDialog({ kind: 'pick' })}>
          新建数据源
        </Button>
      </HeaderActions>
      <Stats>
        <StatCard label="数据源" value={list.data?.total ?? 0} />
        <StatCard label="启用中" value={items.filter((d) => d.status === 'active').length} />
        <StatCard label="表" value={tables.reduce((n, t) => n + (t.data?.length ?? 0), 0)} />
        <StatCard label="需要处理" value={needsWork} help="有未处理的字段变化，或连接账号不是只读" />
      </Stats>
      <div className="cb-filters">
        <Segmented
          label="数据源类型"
          value={filter}
          onChange={(v) => {
            setFilter(v)
            paged.setPage(1)
          }}
          items={[
            { id: 'all', label: <>全部 <span className="cb-num">{items.length}</span></> },
            { id: 'db', label: <>数据库 <span className="cb-num">{dbCount}</span></> },
            { id: 'upload', label: <>上传文件 <span className="cb-num">{items.length - dbCount}</span></> },
          ]}
        />
        <span className="cb-spacer" />
        <div className="cb-search">
          <TextField
            aria-label="搜索数据源"
            placeholder="按名称或地址搜索"
            icon="search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              paged.setPage(1)
            }}
          />
        </div>
      </div>
      <ListCard label="数据源列表" pager={<Pager {...paged.pager} />}>
        <ListStatus
          loading={list.isPending}
          error={list.error}
          onRetry={() => void list.refetch()}
          what="数据源"
          count={shown.length}
          filtered={!!needle || filter !== 'all'}
          query={needle ? q.trim() : undefined}
          onClear={() => {
            setQ('')
            setFilter('all')
          }}
          cols={6}
          empty={
            <EmptyState icon="database" title="还没有数据源" action={<Button variant="primary" icon="plus" onClick={() => setDialog({ kind: 'pick' })}>新建数据源</Button>}>
              连接数据库或上传 Excel / CSV 后，员工就可以用一句话提问。
            </EmptyState>
          }
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                <th style={{ width: '16%' }}>名称</th>
                <th style={{ width: '13%' }}>类型</th>
                <th>地址</th>
                <th style={{ width: 64 }}>表数</th>
                <th style={{ width: 112 }}>最近同步</th>
                <th style={{ width: '16%' }}>状态</th>
                <th style={{ width: 244 }}>
                  <span className="cb-sr">操作</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {paged.rows.map(({ d, i }) => {
                const ts = tablesOf(i)
                const jobs = uploads[i]?.data
                const synced = lastSync(ts)
                return (
                  <tr key={d.id} className={d.status === 'disabled' ? 'cb-row-off' : undefined}>
                    <td title={d.name}>
                      <button type="button" className="cb-link" onClick={() => setDialog({ kind: 'detail', datasource: d })}>
                        {d.name}
                      </button>
                    </td>
                    <td>
                      <KindTag kind={d.type} />
                    </td>
                    {d.type === 'upload' ? (
                      <td className="cb-muted">
                        内置库 · {jobs ? `${new Set(jobs.map((j) => j.filename)).size} 个文件` : '…'}
                        {jobs?.length ? (
                          <>
                            {' · '}
                            <button type="button" className="cb-link" onClick={() => setDialog({ kind: 'history', datasource: d, jobs })}>
                              历史
                            </button>
                          </>
                        ) : null}
                      </td>
                    ) : (
                      <td className="cb-m" title={`${d.host}:${d.port}/${d.database}`}>{`${d.host}:${d.port}/${d.database}`}</td>
                    )}
                    <td className="cb-r">{ts ? ts.length : '—'}</td>
                    <td className="cb-r" title={synced ?? undefined}>
                      {synced ? formatTime(synced) : '—'}
                    </td>
                    <td>{statusOf(d)}</td>
                    <td>
                      <span className="cb-slots">
                        {d.type === 'upload' ? (
                          <>
                            <span />
                            <span />
                            <Button size="sm" variant="ghost" icon="upload" onClick={() => setDialog({ kind: 'upload', datasource: d })}>
                              上传数据
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button size="sm" variant="ghost" onClick={() => void test(d)}>
                              测试
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => edit(d)}>
                              编辑
                            </Button>
                            <Button size="sm" variant="ghost" icon="refresh" disabled={syncing.has(d.id) || d.status !== 'active'} onClick={() => void sync(d)}>
                              {syncing.has(d.id) ? '同步中' : '同步'}
                            </Button>
                          </>
                        )}
                        <Switch
                          checked={d.status === 'active'}
                          label={d.name}
                          disabled={status.isPending || d.status === 'pending'}
                          title={d.status === 'pending' ? '导入完成后才能启用或停用' : undefined}
                          onChange={(on) =>
                            status.mutate(
                              { id: d.id, status: on ? 'active' : 'disabled' },
                              { onSuccess: () => toast({ tone: 'success', title: on ? `已启用「${d.name}」` : `已停用「${d.name}」`, message: on ? '员工可以对它提问了。' : '停用期间没人能查询它。' }) },
                            )
                          }
                        />
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>

      {dialog?.kind === 'pick' ? (
        <Dialog title="新建数据源" size="lg" onClose={() => setDialog(null)}>
          <p className="cb-muted">选择数据库类型，然后填写连接信息。Excel / CSV 通过上传导入平台内置库。</p>
          <div className="cb-dscards">
            {KINDS.map((k) => (
              <button key={k.kind} type="button" className="cb-dscard" onClick={() => setDialog({ kind: 'form', form: blank(k.kind) })}>
                <img src={k.icon} alt="" />
                <span>
                  <b>{k.label}</b>
                  <small>默认端口 {k.port}</small>
                </span>
              </button>
            ))}
            <button type="button" className="cb-dscard" onClick={() => setDialog({ kind: 'upload' })}>
              <img src={UPLOAD.icon} alt="" />
              <span>
                <b>Excel / CSV</b>
                <small>上传文件，存入内置库</small>
              </span>
            </button>
          </div>
          <DialogFooter>
            <Button onClick={() => setDialog(null)}>取消</Button>
          </DialogFooter>
        </Dialog>
      ) : null}

      {dialog?.kind === 'form' ? (
        <DatasourceForm
          key={dialog.id ?? 'new'}
          id={dialog.id}
          value={dialog.form}
          onBack={dialog.id ? undefined : () => setDialog({ kind: 'pick' })}
          onClose={() => setDialog(null)}
        />
      ) : null}

      {dialog?.kind === 'upload' ? (
        <UploadDialog
          datasourceId={dialog.datasource?.id}
          datasourceName={dialog.datasource?.name}
          onBack={dialog.datasource ? undefined : () => setDialog({ kind: 'pick' })}
          onClose={() => setDialog(null)}
          onDone={uploaded}
        />
      ) : null}

      {dialog?.kind === 'history' ? (
        <Drawer title={`${dialog.datasource.name} · 上传历史`} onClose={() => setDialog(null)}>
          <table className="cb-data">
            <thead>
              <tr>
                <th>文件</th>
                <th>方式</th>
                <th>行数</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {dialog.jobs.map((j) => (
                <tr key={j.id}>
                  <td className="cb-m" title={j.filename}>
                    {j.filename}
                    <span className="cb-sub">版本 {j.version}</span>
                  </td>
                  <td>{j.mode === 'append' ? '追加' : '覆盖'}</td>
                  <td className="cb-r">{j.row_count.toLocaleString('zh-CN')}</td>
                  <td>{j.status === 'success' ? <Badge tone="success">成功</Badge> : j.status === 'failed' ? <Badge tone="danger">失败</Badge> : <Badge tone="info">导入中</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Drawer>
      ) : null}

      {dialog?.kind === 'changes' ? (
        <ChangesDrawer datasource={dialog.datasource} set={changes[dialog.datasource.id]} onClose={() => setDialog(null)} />
      ) : null}

      {dialog?.kind === 'detail' ? <DatasourceDetail datasource={dialog.datasource} onClose={() => setDialog(null)} /> : null}
    </div>
  )
}

/** 字段变化明细：每处变化、类型和影响（哪些语义、指标引用了被删除的字段），处理完点「标记已处理」。 */
function ChangesDrawer(props: { datasource: DatasourceRead; set?: ChangeSet; onClose: () => void }) {
  const toast = useToast()
  const navigate = useNavigate()
  const resolve = useSchemaChanges((s) => s.resolve)
  const semantics = useSemantics(props.datasource.id)
  const metrics = useMetrics(props.datasource.id)
  const changes = props.set?.changes ?? []
  const impact = (c: (typeof changes)[number]) => {
    if (c.kind !== 'remove-column' && c.kind !== 'remove-table') return []
    const out: string[] = []
    const sem = (semantics.data ?? []).filter((s) => s.target_id === c.id && s.target_type === (c.kind === 'remove-table' ? 'table' : 'column'))
    if (sem.length) out.push(`语义「${sem.map((s) => s.aliases?.[0] ?? '').filter(Boolean).join('、') || '已设语义'}」会失效`)
    const word = c.column ?? c.table
    const hit = (metrics.data ?? []).filter((m) => new RegExp(`\\b${word}\\b`).test(m.sql_template))
    if (hit.length) out.push(`指标 ${hit.map((m) => `「${m.name}」`).join('')} 的口径引用了它`)
    return out
  }
  return (
    <Drawer
      title={`${props.datasource.name} · 字段变化`}
      onClose={props.onClose}
      footer={
        <>
          <Button onClick={props.onClose}>关闭</Button>
          <Button
            variant="primary"
            disabled={!changes.length}
            onClick={() => {
              resolve(props.datasource.id)
              toast({ tone: 'success', title: '已标记为已处理', message: props.datasource.name })
              props.onClose()
            }}
          >
            标记已处理
          </Button>
        </>
      }
    >
      {props.set ? <span className="cb-faint">最近一次同步：{formatTime(props.set.at)} · 改名会显示为一删一增</span> : null}
      {changes.length ? (
        <ul className="cb-change-list">
          {changes.map((c) => {
            const hits = impact(c)
            return (
              <li key={`${c.kind}:${c.table}:${c.column ?? ''}`}>
                <div className="cb-change-head">
                  <Badge tone={c.kind.startsWith('remove') ? 'danger' : 'info'}>{CHANGE_LABEL[c.kind]}</Badge>
                  <span className="cb-mono">{c.column ? `${c.table}.${c.column}` : c.table}</span>
                  {c.type ? <span className="cb-faint cb-mono">{c.type}</span> : null}
                </div>
                {hits.map((h) => (
                  <div key={h} className="cb-change-impact">
                    {h}
                  </div>
                ))}
                <div className="cb-row">
                  <Button size="sm" variant="ghost" onClick={() => navigate(`/semantic?ds=${props.datasource.id}&table=${encodeURIComponent(c.table)}`)}>
                    去改语义
                  </Button>
                  {hits.some((h) => h.startsWith('指标')) ? (
                    <Button size="sm" variant="ghost" onClick={() => navigate(`/metrics?ds=${props.datasource.id}`)}>
                      去看指标
                    </Button>
                  ) : null}
                </div>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="cb-muted">没有未处理的字段变化。</p>
      )}
    </Drawer>
  )
}

function DatasourceForm(props: { id?: number; value: DatasourceWrite; onBack?: () => void; onClose: () => void }) {
  const toast = useToast()
  const save = useSaveDatasource()
  const [f, setF] = useState(props.value)
  const [testing, setTesting] = useState(false)
  const set = <K extends keyof DatasourceWrite>(k: K, v: DatasourceWrite[K]) => setF((x) => ({ ...x, [k]: v }))
  const meta = kindMeta(f.type)

  const test = async () => {
    setTesting(true)
    try {
      const r = await testDatasourceDraft(f)
      toast(
        r.available
          ? { tone: r.warning ? 'warning' : 'success', title: '连接正常', message: r.warning ?? '账号为只读，可以保存。' }
          : { tone: 'danger', title: '连接失败', message: '请检查地址、账号和网络后重试。' },
      )
    } catch (e) {
      toast({ tone: 'danger', title: '连接失败', message: e instanceof ApiError ? describeError(e.code) : undefined })
    } finally {
      setTesting(false)
    }
  }

  return (
    <Dialog title={props.id ? '编辑数据源' : `新建数据源 · ${meta?.label ?? ''}`} size="lg" onClose={props.onClose}>
      <div className="cb-form-grid">
        <div className="cb-full">
          <TextField label="名称" value={f.name} onChange={(e) => set('name', e.target.value)} />
        </div>
        <div className="cb-field">
          <label htmlFor="ds-type">类型</label>
          <Select id="ds-type" value={f.type} disabled={!!props.id} onChange={(t) => setF({ ...f, type: t as Kind, port: kindMeta(t)?.port ?? f.port })} options={KINDS.map((k) => ({ value: k.kind, label: k.label }))} />
        </div>
        <TextField label="主机" value={f.host} onChange={(e) => set('host', e.target.value)} />
        <TextField label="端口" type="number" value={f.port} onChange={(e) => set('port', Number(e.target.value))} />
        <TextField label="数据库" value={f.database} onChange={(e) => set('database', e.target.value)} />
        <TextField label="Schema" value={f.schema_name} onChange={(e) => set('schema_name', e.target.value)} />
        <TextField label="只读账号" value={f.username} onChange={(e) => set('username', e.target.value)} />
        <TextField label="密码" type="password" value={f.password} hint={props.id ? '留空表示不修改；测试连接需要重新填写' : undefined} onChange={(e) => set('password', e.target.value)} />
        <label className="cb-full">
          <Switch checked={f.tls} label="TLS 加密连接" onChange={(on) => set('tls', on)} title={f.tls ? '已开启 TLS' : '未开启 TLS'} />
          使用 TLS 加密连接
        </label>
        {meta?.hint ? <div className="cb-full cb-field-hint">{meta.hint}</div> : null}
        {save.error instanceof ApiError ? (
          <div className="cb-full">
            <Callout tone="danger">{describeError(save.error.code)}</Callout>
          </div>
        ) : null}
      </div>
      <DialogFooter left={<Button icon="play" disabled={testing || !f.host || !f.username} onClick={() => void test()}>测试连接</Button>}>
        {props.onBack ? <Button variant="ghost" onClick={props.onBack}>上一步</Button> : null}
        <Button onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={save.isPending || !f.name || !f.host} onClick={() => save.mutate({ id: props.id, body: f }, { onSuccess: () => { toast({ tone: 'success', title: '数据源已保存', message: props.id ? undefined : '点「同步」获取表结构。' }); props.onClose() } })}>保存</Button>
      </DialogFooter>
    </Dialog>
  )
}
