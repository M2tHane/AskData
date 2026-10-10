import { useState } from 'react'
import { testModel, testModelDraft, useModels, usePreference, useSaveModel, useSetModelEnabled, useSetPreference, useTeams } from '../api/hooks'
import type { ModelRead, ModelWrite, Role } from '../api/types'
import { Badge, Button, Callout, Panel, Switch, TextField } from '../components/basic'
import { Dialog, DialogFooter } from '../components/Dialog'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { Select } from '../components/Select'

const KIND_LABEL = { llm: '对话模型', embedding: 'Embedding', rerank: 'Rerank' } as const
type Kind = keyof typeof KIND_LABEL

const EMPTY: ModelWrite = { type: 'llm', provider: 'openai', model_name: '', api_key: '', base_url: null, enabled: true, is_default: false, embedding_dimension: null, params: { api: 'chat', retries: 2, timeout_seconds: 60 } }

export function ModelsPage(props: { role: Role; teamId: number | null }) {
  const admin = props.role === 'admin'
  const models = useModels()
  const save = useSaveModel()
  const enable = useSetModelEnabled()
  const teams = useTeams(admin)
  const [editing, setEditing] = useState<{ id?: number; form: ModelWrite } | null>(null)
  const toast = useToast()
  const [pickedTeam, setPickedTeam] = useState<number | null>(null)
  const teamId = admin ? pickedTeam ?? teams.data?.items[0]?.id ?? null : props.teamId
  const items = models.data?.items ?? []

  const runTest = async (m: ModelRead) => {
    toast({ tone: 'info', title: `${m.model_name}：正在测试` })
    try {
      const r = await testModel(m.id)
      toast(r.available ? { tone: 'success', title: `${m.model_name}：可用` } : { tone: 'danger', title: `${m.model_name}：不可用`, message: '请检查接口地址、密钥、模型名和调用协议。' })
    } catch (e) {
      toast({ tone: 'danger', title: `${m.model_name}：测试失败`, message: e instanceof ApiError ? describeError(e.code) : undefined })
    }
  }

  return (
    <div className="cb-page">
      <Panel title="模型" actions={admin ? <Button variant="primary" icon="plus" onClick={() => setEditing({ form: EMPTY })}>添加模型</Button> : undefined}>
        <table className="cb-data cb-data-fixed">
          <thead><tr><th style={{ width: 120 }}>类型</th><th>模型</th><th style={{ width: 120 }}>服务商</th><th style={{ width: 140 }}>密钥</th>{admin ? <th style={{ width: 200 }} /> : null}</tr></thead>
          <tbody>
            {items.map((m: ModelRead) => {
              return (
                <tr key={m.id} className={m.enabled ? undefined : 'cb-row-off'}>
                  <td>{KIND_LABEL[m.type]}</td>
                  <td className="cb-mono">{m.model_name}{m.is_default ? <Badge tone="info">默认</Badge> : null}{!admin && !m.enabled ? <Badge>已停用</Badge> : null}</td>
                  <td>{m.provider}</td>
                  <td className="cb-mono">{m.api_key}</td>
                  {admin ? (
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      <span className="cb-row-actions">
                      <Button size="sm" variant="ghost" onClick={() => void runTest(m)}>测试连接</Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing({ id: m.id, form: { type: m.type, provider: m.provider, model_name: m.model_name, api_key: '', base_url: m.base_url ?? null, enabled: m.enabled ?? true, is_default: m.is_default ?? false, embedding_dimension: m.embedding_dimension ?? null, params: { ...EMPTY.params, ...m.params } } })}>编辑</Button>
                      <Switch
                        checked={m.enabled}
                        label={m.model_name}
                        disabled={enable.isPending}
                        onChange={(on) =>
                          enable.mutate(
                            { id: m.id, enabled: on },
                            { onError: (e) => toast({ tone: 'danger', title: `${m.model_name}：无法${on ? '启用' : '停用'}`, message: e instanceof ApiError && e.code === 'CONFLICT' ? '有团队正在使用这个模型，请先在「团队默认模型」里换掉。' : e instanceof ApiError ? describeError(e.code) : undefined }) },
                          )
                        }
                      />
                      </span>
                    </td>
                  ) : null}
                </tr>
              )
            })}
          </tbody>
        </table>
        {models.data && !items.length ? <div className="cb-muted">还没有模型。没有对话模型时查数会返回「模型不可用」。</div> : null}
      </Panel>

      {teamId != null ? (
        <Panel
          title="团队默认模型"
          actions={
            admin ? (
              <Select ariaLabel="团队" value={teamId} onChange={setPickedTeam} options={(teams.data?.items ?? []).map((t) => ({ value: t.id, label: t.name }))} />
            ) : undefined
          }
        >
          <div className="cb-col">
            {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
              <PreferenceRow key={k} kind={k} teamId={teamId} options={items.filter((m) => m.type === k && m.enabled)} />
            ))}
          </div>
        </Panel>
      ) : null}

      {editing ? (
        <Dialog title={editing.id ? '编辑模型' : '添加模型'} onClose={() => setEditing(null)}>
          <ModelForm
            value={editing.form}
            editing={!!editing.id}
            busy={save.isPending}
            error={save.error instanceof ApiError ? save.error.message : null}
            onCancel={() => setEditing(null)}
            onSave={(form) => save.mutate({ id: editing.id, body: form }, { onSuccess: () => setEditing(null) })}
          />
        </Dialog>
      ) : null}
    </div>
  )
}

function PreferenceRow(props: { kind: Kind; teamId: number; options: ModelRead[] }) {
  const pref = usePreference(props.teamId, props.kind)
  const set = useSetPreference(props.teamId, props.kind)
  return (
    <div className="cb-row">
      <span style={{ width: 'var(--sidebar-width)' }}>{KIND_LABEL[props.kind]}</span>
      <Select
        ariaLabel={`${KIND_LABEL[props.kind]}默认`}
        value={pref.data?.model_id ?? null}
        placeholder="未设置"
        disabled={set.isPending}
        onChange={(id) => set.mutate(id)}
        options={props.options.map((m) => ({ value: m.id, label: m.model_name }))}
      />
      {set.error instanceof ApiError ? <Badge tone="danger" icon="alert">{set.error.message}</Badge> : null}
    </div>
  )
}

function ModelForm(props: { value: ModelWrite; editing: boolean; busy: boolean; error: string | null; onCancel: () => void; onSave: (v: ModelWrite) => void }) {
  const toast = useToast()
  const [f, setF] = useState(props.value)
  const [testing, setTesting] = useState(false)
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
  const params = { ...EMPTY.params, ...f.params } as NonNullable<ModelWrite['params']> & { retries: number; timeout_seconds: number }
  return (
    <div className="cb-form">
      <div className="cb-field">
        <label htmlFor="m-type">类型</label>
        <Select id="m-type" value={f.type} onChange={(type) => setF({ ...f, type })} options={(Object.keys(KIND_LABEL) as Kind[]).map((k) => ({ value: k, label: KIND_LABEL[k] }))} />
      </div>
      <TextField label="服务商" value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })} />
      <TextField label="模型名" value={f.model_name} onChange={(e) => setF({ ...f, model_name: e.target.value })} />
      <TextField label="接口地址" hint="OpenAI 兼容接口填写 base_url，可留空" value={f.base_url ?? ''} onChange={(e) => setF({ ...f, base_url: e.target.value || null })} />
      <TextField label="API Key" type="password" value={f.api_key} hint={props.editing ? '必须重新填写，保存后只显示掩码' : '保存后只显示掩码'} onChange={(e) => setF({ ...f, api_key: e.target.value })} />
      <div className="cb-field">
        <label htmlFor="m-api">调用协议</label>
        <Select id="m-api" value={params.api ?? 'chat'} onChange={(api) => setF({ ...f, params: { ...params, api } })} options={[{ value: 'chat', label: 'chat completions' }, { value: 'responses', label: 'responses' }]} />
      </div>
      {f.type === 'embedding' ? <TextField label="向量维度" type="number" value={f.embedding_dimension ?? ''} onChange={(e) => setF({ ...f, embedding_dimension: e.target.value ? Number(e.target.value) : null })} /> : null}
      <label className="cb-radio"><input type="checkbox" checked={f.enabled ?? true} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />启用</label>
      <label className="cb-radio"><input type="checkbox" checked={f.is_default ?? false} onChange={(e) => setF({ ...f, is_default: e.target.checked })} />设为该类型默认</label>
      {props.error ? <Callout tone="danger">{props.error}</Callout> : null}
      <DialogFooter left={<Button icon="play" disabled={testing || !f.model_name || !f.api_key} onClick={() => void test()}>测试连接</Button>}>
        <Button variant="ghost" onClick={props.onCancel}>取消</Button>
        <Button variant="primary" disabled={props.busy || !f.model_name || !f.api_key} onClick={() => props.onSave(f)}>保存模型</Button>
      </DialogFooter>
    </div>
  )
}
