import { useState } from 'react'
import { useDeleteMetric, useMetrics, usePromote, usePromotions, useReviewPromotion, useSaveMetric } from '../api/hooks'
import type { MetricInput, Role } from '../api/types'
import { Badge, Button, Callout, Panel, ScopeTag, TextField } from '../components/basic'
import { DatasourcePicker } from '../components/DatasourcePicker'
import { Dialog, DialogFooter } from '../components/Dialog'
import { ApiError } from '../lib/errors'
import { OWN_SCOPE, SCOPE_NAME, STATUS_LABEL, canEdit } from '../lib/scope'

export function MetricsPage({ role }: { role: Role }) {
  const [ds, setDs] = useState<number | null>(null)
  const list = useMetrics(ds)
  const save = useSaveMetric(ds)
  const del = useDeleteMetric(ds)
  const promote = usePromote()
  const promotions = usePromotions()
  const review = useReviewPromotion()
  const [editing, setEditing] = useState<{ id?: number; form: MetricInput } | null>(null)
  const pending = (promotions.data ?? []).filter((p) => p.status === 'pending')

  return (
    <div className="cb-page">
      {role !== 'member' && pending.length ? (
        <Panel title="待审核的提升申请" eyebrow="PROMOTION">
          <table className="cb-data">
            <tbody>
              {pending.map((p) => (
                <tr key={p.id}>
                  <td><Badge tone="warning" icon="alert">待审核</Badge></td>
                  <td>{p.kind === 'metric' ? '指标' : '语义'} #{p.ref_id}</td>
                  <td>{p.comment}</td>
                  <td style={{ textAlign: 'right' }}>
                    <Button size="sm" variant="ghost" onClick={() => review.mutate({ id: p.id, approve: false })}>驳回</Button>
                    <Button size="sm" variant="primary" onClick={() => review.mutate({ id: p.id, approve: true })}>通过</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}
      <Panel
        title="指标"
        actions={
          <div className="cb-row">
            <DatasourcePicker value={ds} onChange={setDs} />
            <Button
              variant="primary"
              icon="plus"
              disabled={ds == null}
              onClick={() => ds != null && setEditing({ form: { datasource_id: ds, name: '', aliases: [], description: '', sql_template: '', params: {}, scope: OWN_SCOPE[role] } })}
            >
              添加指标
            </Button>
          </div>
        }
      >
        <table className="cb-data cb-data-fixed">
          <colgroup><col style={{ width: '16%' }} /><col style={{ width: '40%' }} /><col style={{ width: '16%' }} /><col style={{ width: '12%' }} /><col style={{ width: '16%' }} /></colgroup>
          <thead>
            <tr><th>名称</th><th>口径</th><th>级别</th><th>最近校验</th><th /></tr>
          </thead>
          <tbody>
            {(list.data ?? []).map((m) => (
              <tr key={m.id}>
                <td title={`${m.name} ${(m.aliases ?? []).join('、')}`}>
                  <div className="body-strong">{m.name}</div>
                  <div className="cb-muted">{(m.aliases ?? []).join('、')}</div>
                </td>
                <td className="cb-wrap"><code className="code-sm cb-sql">{m.sql_template}</code></td>
                <td>
                  <span className="cb-row-actions" style={{ justifyContent: 'flex-start' }}>
                    <ScopeTag level={m.scope} />
                    {m.status === 'pending' ? <Badge tone="warning">{STATUS_LABEL.pending}</Badge> : null}
                  </span>
                </td>
                <td className="cb-muted cb-num">{m.last_validated_at?.slice(0, 10)}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                  {canEdit(m, role) ? (
                  <Button size="sm" variant="ghost" onClick={() => setEditing({ id: m.id, form: { datasource_id: m.datasource_id, name: m.name, aliases: m.aliases ?? [], description: m.description, sql_template: m.sql_template, params: (m.params ?? {}) as MetricInput['params'], scope: m.scope } })}>编辑</Button>
                  ) : (
                    <span className="cb-muted" title={`${SCOPE_NAME[m.scope]}指标由${m.scope === 'platform' ? '管理员' : m.scope === 'team' ? '团队负责人' : '创建者'}维护`}>只读</span>
                  )}
                  {m.scope === 'personal' && m.status === 'private' && role !== 'admin' ? (
                    <Button size="sm" variant="ghost" onClick={() => promote.mutate({ kind: 'metric', ref_id: m.id })}>申请提升</Button>
                  ) : null}
                  {canEdit(m, role) ? <Button size="sm" variant="ghost" onClick={() => del.mutate(m.id)}>删除</Button> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {list.data && !list.data.length ? <div className="cb-muted">还没有指标。指标保存时会用只读账号试跑校验。</div> : null}
      </Panel>
      {editing ? (
        <Dialog title={editing.id ? '编辑指标' : '添加指标'} onClose={() => setEditing(null)}>
          <MetricForm
            role={role}
            value={editing.form}
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

function MetricForm(props: { role: Role; value: MetricInput; busy: boolean; error: string | null; onCancel: () => void; onSave: (v: MetricInput) => void }) {
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
