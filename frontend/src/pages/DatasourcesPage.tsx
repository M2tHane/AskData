import { useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { runSync, testDatasource, testDatasourceDraft, useDatasources, useSaveDatasource, useSetDatasourceStatus } from '../api/hooks'
import type { DatasourceRead, DatasourceWrite } from '../api/types'
import { Badge, Button, Callout, Panel, Switch, TextField } from '../components/basic'
import { DatasourceDetail } from '../components/DatasourceDetail'
import { Dialog, DialogFooter } from '../components/Dialog'
import { UploadDialog } from '../components/UploadDialog'
import { Select } from '../components/Select'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'

type Kind = 'mysql' | 'postgresql' | 'kingbase' | 'gaussdb' | 'clickhouse'

export const KINDS: { kind: Kind; label: string; icon: string; port?: number; hint?: string }[] = [
  { kind: 'mysql', label: 'MySQL', icon: '/datasource-icons/icon_mysql.png', port: 3306 },
  { kind: 'postgresql', label: 'PostgreSQL', icon: '/datasource-icons/icon_PostgreSQL.png', port: 5432 },
  { kind: 'kingbase', label: '人大金仓 KingbaseES', icon: '/datasource-icons/icon_kingbase.svg', port: 54321 },
  { kind: 'gaussdb', label: 'GaussDB / openGauss', icon: '/datasource-icons/icon_gaussdb.svg', port: 8000, hint: 'openGauss 默认端口 5432' },
  { kind: 'clickhouse', label: 'ClickHouse', icon: '/datasource-icons/icon_clickhouse.svg', port: 8123, hint: '使用 HTTP 端口' },
]

/** 上传的 Excel / CSV 存在平台内置库里，不需要连接信息。 */
const UPLOAD: { kind: string; label: string; icon: string; port?: number; hint?: string } = { kind: 'upload', label: 'Excel / CSV', icon: '/datasource-icons/icon_excel.png' }

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
  | null

export function DatasourcesPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const list = useDatasources()
  const status = useSetDatasourceStatus()
  const [dialog, setDialog] = useState<Dialogs>(null)
  const [syncing, setSyncing] = useState<Set<number>>(new Set())
  const items = list.data?.items ?? []

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
      const r = await runSync(d.id)
      toast(
        r.status === 'success'
          ? { tone: 'success', title: `${d.name}：同步完成`, message: '新增的表和字段默认对团队和员工可见，可在表与字段里调整。' }
          : { tone: 'danger', title: `${d.name}：同步失败`, message: r.error ? describeError(r.error) : '请稍后重试。' },
      )
      void qc.invalidateQueries({ queryKey: ['tables', d.id] })
      void qc.invalidateQueries({ queryKey: ['grants', d.id] })
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
  }, [qc])

  const edit = (d: DatasourceRead) =>
    setDialog({
      kind: 'form',
      id: d.id,
      form: { name: d.name, type: d.type as DatasourceWrite['type'], host: d.host, port: d.port, database: d.database, schema_name: d.schema_name, username: d.username, password: '', tls: d.tls },
    })

  return (
    <div className="cb-page">
      <div className="cb-grid-stats">
        <Panel><div className="cb-stat"><b>{list.data?.total ?? 0}</b><span>数据源</span></div></Panel>
        <Panel><div className="cb-stat"><b>{items.filter((d) => d.status === 'active').length}</b><span>启用中</span></div></Panel>
      </div>
      <Panel title="数据源" actions={<Button variant="primary" icon="plus" onClick={() => setDialog({ kind: 'pick' })}>新建数据源</Button>}>
        <table className="cb-data cb-data-fixed">
          <thead>
            <tr>
              <th style={{ width: '22%' }}>名称</th>
              <th style={{ width: '18%' }}>类型</th>
              <th>地址</th>
              <th style={{ width: 320 }} />
            </tr>
          </thead>
          <tbody>
            {items.map((d) => {
              return (
                <tr key={d.id} className={d.status === 'disabled' ? 'cb-row-off' : undefined}>
                  <td title={d.name}>
                    <button type="button" className="cb-link" onClick={() => setDialog({ kind: 'detail', datasource: d })}>{d.name}</button>
                  </td>
                  <td><KindTag kind={d.type} /></td>
                  {d.type === 'upload' ? (
                    <td className="cb-muted">平台内置库 · {d.schema_name}</td>
                  ) : (
                    <td className="cb-mono" title={`${d.host}:${d.port}/${d.database}`}>{`${d.host}:${d.port}/${d.database}`}</td>
                  )}
                  <td style={{ textAlign: 'right' }}>
                    <span className="cb-row-actions">
                      {d.type === 'upload' ? (
                        <Button size="sm" variant="ghost" icon="upload" onClick={() => setDialog({ kind: 'upload', datasource: d })}>上传数据</Button>
                      ) : (
                        <>
                          <Button size="sm" variant="ghost" onClick={() => void test(d)}>测试</Button>
                          <Button size="sm" variant="ghost" onClick={() => edit(d)}>编辑</Button>
                          <Button size="sm" variant="ghost" icon="refresh" disabled={syncing.has(d.id) || d.status !== 'active'} onClick={() => void sync(d)}>同步</Button>
                        </>
                      )}
                      {d.status === 'pending' ? (
                        <Badge tone="warning">待导入</Badge>
                      ) : (
                        <Switch checked={d.status === 'active'} label={d.name} disabled={status.isPending} onChange={(on) => status.mutate({ id: d.id, status: on ? 'active' : 'disabled' })} />
                      )}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {list.data && !items.length ? <div className="cb-muted">还没有数据源，点「新建数据源」选择类型后开始。</div> : null}
      </Panel>

      {dialog?.kind === 'pick' ? (
        <Dialog title="新建数据源" size="lg" onClose={() => setDialog(null)}>
          <div className="cb-muted">连接数据库</div>
          <div className="cb-kinds">
            {KINDS.map((k) => (
              <button key={k.kind} type="button" className="cb-kind" onClick={() => setDialog({ kind: 'form', form: blank(k.kind) })}>
                <img src={k.icon} alt="" />
                {k.label}
              </button>
            ))}
          </div>
          <div className="cb-muted">或上传文件</div>
          <div className="cb-kinds">
            <button type="button" className="cb-kind" onClick={() => setDialog({ kind: 'upload' })}>
              <img src={UPLOAD.icon} alt="" />
              {UPLOAD.label}
            </button>
          </div>
          <DialogFooter><Button variant="ghost" onClick={() => setDialog(null)}>取消</Button></DialogFooter>
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

      {dialog?.kind === 'detail' ? <DatasourceDetail datasource={dialog.datasource} onClose={() => setDialog(null)} /> : null}
    </div>
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
    <Dialog title={props.id ? '编辑数据源' : `新建数据源 · ${meta?.label ?? ''}`} onClose={props.onClose}>
      <div className="cb-form" style={{ maxWidth: 'none' }}>
        <TextField label="名称" value={f.name} onChange={(e) => set('name', e.target.value)} />
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
        {save.error instanceof ApiError ? <Callout tone="danger">{describeError(save.error.code)}</Callout> : null}
      </div>
      <DialogFooter left={<Button icon="play" disabled={testing || !f.host || !f.username} onClick={() => void test()}>测试连接</Button>}>
        {props.onBack ? <Button variant="ghost" onClick={props.onBack}>上一步</Button> : null}
        <Button variant="ghost" onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={save.isPending || !f.name || !f.host} onClick={() => save.mutate({ id: props.id, body: f }, { onSuccess: () => { toast({ tone: 'success', title: '数据源已保存', message: props.id ? undefined : '点「同步」获取表结构。' }); props.onClose() } })}>保存数据源</Button>
      </DialogFooter>
    </Dialog>
  )
}
