import { useState } from 'react'
import { testModel, testModelDraft, useModels, useSaveModel, useSetModelEnabled } from '../api/hooks'
import type { ModelRead, ModelWrite, Role } from '../api/types'
import { Badge, Button, Callout, Segmented, Switch, TextField } from '../components/basic'
import { ConfirmDialog, Dialog, DialogFooter } from '../components/Dialog'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { HeaderActions } from '../components/PageHeader'
import { Pager, usePaged } from '../components/Pager'
import { Select } from '../components/Select'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'

const KIND_LABEL = { llm: '对话模型', embedding: 'Embedding', rerank: 'Rerank' } as const
type Kind = keyof typeof KIND_LABEL
type Filter = 'all' | Kind
type Params = NonNullable<ModelWrite['params']>
type Effort = 'default' | 'low' | 'medium' | 'high'

const EMPTY: ModelWrite = { type: 'llm', provider: 'openai', model_name: '', api_key: '', base_url: null, enabled: true, is_default: false, embedding_dimension: null, params: { api: 'chat', retries: 2, timeout_seconds: 60 } }

const CONFLICT_TEXT = '这个模型是某个团队的默认模型，不能直接停用。请先联系管理员为该团队换一个同类型的模型，再停用它。'

export function ModelsPage(props: { role: Role; teamId: number | null }) {
  const admin = props.role === 'admin'
  const models = useModels()
  const save = useSaveModel()
  const enable = useSetModelEnabled()
  const [editing, setEditing] = useState<{ id?: number; form: ModelWrite } | null>(null)
  const [disabling, setDisabling] = useState<ModelRead | null>(null)
  const [testing, setTesting] = useState<number | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const toast = useToast()
  const items = models.data?.items ?? []
  const count = (k: Kind) => items.filter((m) => m.type === k).length
  const shown = filter === 'all' ? items : items.filter((m) => m.type === filter)
  const paged = usePaged(shown)

  const runTest = async (m: ModelRead) => {
    setTesting(m.id)
    toast({ tone: 'info', title: `${m.model_name}：正在测试` })
    try {
      const r = await testModel(m.id)
      toast(r.available ? { tone: 'success', title: `${m.model_name}：可用` } : { tone: 'danger', title: `${m.model_name}：不可用`, message: '请检查接口地址、密钥、模型名和调用协议。' })
    } catch (e) {
      toast({ tone: 'danger', title: `${m.model_name}：测试失败`, message: e instanceof ApiError ? describeError(e.code) : undefined })
    } finally {
      setTesting(null)
    }
  }

  const setEnabled = (m: ModelRead, on: boolean) =>
    enable.mutate(
      { id: m.id, enabled: on },
      {
        onSuccess: () => {
          setDisabling(null)
          toast({ tone: 'success', title: `${m.model_name}：${on ? '已启用' : '已停用'}` })
        },
        onError: (e) => {
          setDisabling(null)
          toast({ tone: 'danger', title: `${m.model_name}：无法${on ? '启用' : '停用'}`, message: e instanceof ApiError && e.code === 'CONFLICT' ? CONFLICT_TEXT : e instanceof ApiError ? describeError(e.code) : undefined })
        },
      },
    )

  const edit = (m: ModelRead) =>
    setEditing({ id: m.id, form: { type: m.type, provider: m.provider, model_name: m.model_name, api_key: '', base_url: m.base_url ?? null, enabled: m.enabled ?? true, is_default: m.is_default ?? false, embedding_dimension: m.embedding_dimension ?? null, params: { ...EMPTY.params, ...m.params } } })

  const addButton = (
    <Button variant="primary" icon="plus" onClick={() => setEditing({ form: EMPTY })}>添加模型</Button>
  )

  return (
    <div className="cb-page">
      {admin ? <HeaderActions>{addButton}</HeaderActions> : null}
      <div className="cb-filters">
        <Segmented
          label="模型类型"
          value={filter}
          onChange={(v) => {
            setFilter(v)
            paged.setPage(1)
          }}
          items={[
            { id: 'all', label: <>全部 <span className="cb-num">{items.length}</span></> },
            ...(Object.keys(KIND_LABEL) as Kind[]).map((k) => ({ id: k, label: <>{KIND_LABEL[k]} <span className="cb-num">{count(k)}</span></> })),
          ]}
        />
      </div>
      <ListCard label="模型列表" pager={<Pager {...paged.pager} />}>
        <ListStatus
          loading={models.isPending}
          error={models.error}
          onRetry={() => void models.refetch()}
          what="模型"
          count={shown.length}
          filtered={filter !== 'all'}
          onClear={() => setFilter('all')}
          cols={5}
          empty={
            <EmptyState icon="cpu" title="还没有模型" action={admin ? addButton : undefined}>
              没有对话模型时，员工提问会返回「模型不可用」。
            </EmptyState>
          }
        >
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr>
                <th style={{ width: 112 }}>类型</th>
                <th style={{ width: '30%' }}>模型 · 服务商</th>
                <th style={{ width: 232 }}>接口地址</th>
                <th>密钥</th>
                {admin ? <th style={{ width: 176 }}><span className="cb-sr">操作</span></th> : null}
              </tr>
            </thead>
            <tbody>
              {paged.rows.map((m) => (
                <tr key={m.id} className={m.enabled ? undefined : 'cb-row-off'}>
                  <td>{KIND_LABEL[m.type]}</td>
                  <td title={`${m.model_name} · ${m.provider}`}>
                    <span className="cb-model-name">
                      <span className="cb-m cb-model-text">{m.model_name}</span>
                      {m.is_default ? <Badge tone="info">默认</Badge> : null}
                      {!admin && !m.enabled ? <Badge>已停用</Badge> : null}
                    </span>
                    <span className="cb-sub">{m.provider}</span>
                  </td>
                  <td className="cb-m" title={m.base_url ?? undefined}>{m.base_url ?? <span className="cb-faint">—</span>}</td>
                  <td className="cb-m">{m.api_key}</td>
                  {admin ? (
                    <td>
                      <span className="cb-actions">
                        <Button size="sm" variant="ghost" disabled={testing === m.id} onClick={() => void runTest(m)}>{testing === m.id ? '测试中' : '测试'}</Button>
                        <Button size="sm" variant="ghost" onClick={() => edit(m)}>编辑</Button>
                        <Switch checked={m.enabled} label={m.model_name} disabled={enable.isPending} onChange={(on) => (on ? setEnabled(m, true) : setDisabling(m))} />
                      </span>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </ListStatus>
      </ListCard>

      {editing ? (
        <Dialog title={editing.id ? '编辑模型' : '添加模型'} size="lg" onClose={() => setEditing(null)}>
          <ModelForm
            key={editing.id ?? 'new'}
            value={editing.form}
            editing={!!editing.id}
            busy={save.isPending}
            error={save.error instanceof ApiError ? describeError(save.error.code) : null}
            onCancel={() => setEditing(null)}
            onSave={(form) =>
              save.mutate(
                { id: editing.id, body: form },
                {
                  onSuccess: () => {
                    toast({ tone: 'success', title: editing.id ? `${form.model_name}：已保存` : `${form.model_name}：已添加` })
                    setEditing(null)
                  },
                },
              )
            }
          />
        </Dialog>
      ) : null}
      {disabling ? (
        <ConfirmDialog title={`停用「${disabling.model_name}」`} confirmLabel="停用模型" danger busy={enable.isPending} onConfirm={() => setEnabled(disabling, false)} onClose={() => setDisabling(null)}>
          停用后，这个模型不再响应查数请求。如果它是某个团队的默认模型，需要先为该团队换掉。
        </ConfirmDialog>
      ) : null}
    </div>
  )
}

function ModelForm(props: { value: ModelWrite; editing: boolean; busy: boolean; error: string | null; onCancel: () => void; onSave: (v: ModelWrite) => void }) {
  const toast = useToast()
  const [f, setF] = useState(props.value)
  const [testing, setTesting] = useState(false)
  const params = { ...EMPTY.params, ...f.params } as Params
  const setParams = (patch: Partial<Params>) => setF({ ...f, params: { ...params, ...patch } })
  const num = (v: string, fallback: number) => (v === '' ? fallback : Math.max(0, Number(v)))
  const ready = !!f.model_name.trim() && !!f.provider.trim() && !!f.api_key
  const test = async () => {
    setTesting(true)
    try {
      const r = await testModelDraft(f)
      toast(r.available ? { tone: 'success', title: '模型可用', message: `${f.model_name} 已成功响应。` } : { tone: 'danger', title: '模型不可用', message: '请检查接口地址、密钥、模型名和调用协议。' })
    } catch (e) {
      toast({ tone: 'danger', title: '测试失败', message: e instanceof ApiError ? describeError(e.code) : undefined })
    } finally {
      setTesting(false)
    }
  }
  return (
    <>
      <div className="cb-form-grid">
        <div className="cb-field">
          <label htmlFor="m-type">类型</label>
          <Select id="m-type" value={f.type} disabled={props.editing} onChange={(type) => setF({ ...f, type })} options={(Object.keys(KIND_LABEL) as Kind[]).map((k) => ({ value: k, label: KIND_LABEL[k] }))} />
          {props.editing ? <div className="cb-field-hint">类型创建后不能修改</div> : null}
        </div>
        <TextField label="服务商" value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })} />
        <TextField label="模型名" value={f.model_name} onChange={(e) => setF({ ...f, model_name: e.target.value })} />
        <TextField label="接口地址" hint="OpenAI 兼容接口填写 base_url，可留空" value={f.base_url ?? ''} onChange={(e) => setF({ ...f, base_url: e.target.value || null })} />
        <div className="cb-full">
          <TextField label="API Key" type="password" autoComplete="new-password" value={f.api_key} hint={props.editing ? '保存时需要重新填写密钥，保存后只显示掩码' : '保存后只显示掩码'} onChange={(e) => setF({ ...f, api_key: e.target.value })} />
        </div>
        <div className="cb-field">
          <label htmlFor="m-api">调用协议</label>
          <Select id="m-api" value={params.api ?? 'chat'} onChange={(api) => setParams({ api })} options={[{ value: 'chat', label: 'chat completions' }, { value: 'responses', label: 'responses' }]} />
        </div>
        {f.type === 'llm' ? (
          <div className="cb-field">
            <label htmlFor="m-effort">推理强度</label>
            <Select
              id="m-effort"
              value={(params.reasoning_effort ?? 'default') as Effort}
              onChange={(v) => setParams({ reasoning_effort: v === 'default' ? null : v })}
              options={[{ value: 'default', label: '不指定' }, { value: 'low', label: '低' }, { value: 'medium', label: '中' }, { value: 'high', label: '高' }]}
            />
          </div>
        ) : null}
        {f.type === 'embedding' ? <TextField label="向量维度" type="number" value={f.embedding_dimension ?? ''} onChange={(e) => setF({ ...f, embedding_dimension: e.target.value ? Number(e.target.value) : null })} /> : null}
        <TextField label="超时（秒）" type="number" min={1} value={params.timeout_seconds} onChange={(e) => setParams({ timeout_seconds: num(e.target.value, 30) })} />
        <TextField label="失败重试（次）" type="number" min={0} value={params.retries} onChange={(e) => setParams({ retries: num(e.target.value, 0) })} />
        <label className="cb-radio cb-full"><input type="checkbox" checked={f.enabled ?? true} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />启用</label>
        <label className="cb-radio cb-full"><input type="checkbox" checked={f.is_default ?? false} onChange={(e) => setF({ ...f, is_default: e.target.checked })} />设为该类型默认</label>
      </div>
      {props.error ? <Callout tone="danger">{props.error}</Callout> : null}
      <DialogFooter left={<Button icon="play" disabled={testing || !ready} onClick={() => void test()}>测试连接</Button>}>
        <Button onClick={props.onCancel}>取消</Button>
        <Button variant="primary" disabled={props.busy || !ready} onClick={() => props.onSave(f)}>保存模型</Button>
      </DialogFooter>
    </>
  )
}
