import { useMemo, useState } from 'react'
import { usePromote, useSemantics, useTables } from '../api/hooks'
import { api } from '../api/client'
import type { Role, ScopeLevel, SemanticView, TableRead } from '../api/types'
import { Badge, Button, Callout, Panel } from '../components/basic'
import { DatasourcePicker } from '../components/DatasourcePicker'
import { Dialog, DialogFooter } from '../components/Dialog'
import { useToast } from '../components/Toast'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../lib/errors'
import { OWN_SCOPE, SCOPE_NAME } from '../lib/scope'

export interface Row {
  key: string
  targetType: 'table' | 'column'
  targetId: number
  name: string
  type: string
  alias: string
  description: string
  existing?: SemanticView
  /** 其他级别（平台级、团队级等）已生效的定义；只读，提问时与本级定义按 个人 > 团队 > 平台 取用。 */
  inherited: SemanticView[]
}

export const splitAliases = (text: string) => text.split(/[、,，\s]+/).filter(Boolean)

/** 选定表和级别后，把表本身与全部字段列成可填写的行；已有同级别语义会带入，其他级别的定义作为只读参考。 */
export function buildRows(table: TableRead, scope: ScopeLevel, semantics: SemanticView[]): Row[] {
  const of = (type: 'table' | 'column', id: number) => semantics.filter((s) => s.target_type === type && s.target_id === id)
  const make = (type: 'table' | 'column', id: number, name: string, dataType: string): Row => {
    const all = of(type, id)
    const existing = all.find((s) => s.scope === scope)
    const inherited = all.filter((s) => s.scope !== scope)
    return { key: `${type}:${id}`, targetType: type, targetId: id, name, type: dataType, alias: (existing?.aliases ?? []).join('、'), description: existing?.description ?? '', existing, inherited }
  }
  return [make('table', table.id, `${table.name}（表）`, '表'), ...(table.columns ?? []).map((c) => make('column', c.id, c.name, c.data_type))]
}

/** 有内容的行写入（新建或更新），原来有内容现在清空的行删除，其余跳过。 */
export function planChanges(rows: Row[]): { save: Row[]; remove: Row[] } {
  const filled = (r: Row) => r.alias.trim() !== '' || r.description.trim() !== ''
  return { save: rows.filter(filled), remove: rows.filter((r) => !filled(r) && r.existing) }
}

export function SemanticPage({ role }: { role: Role }) {
  const toast = useToast()
  const qc = useQueryClient()
  const [ds, setDs] = useState<number | null>(null)
  const list = useSemantics(ds)
  const tables = useTables(ds)
  const promote = usePromote()
  const [editing, setEditing] = useState<TableRead | null>(null)
  const scope: ScopeLevel = OWN_SCOPE[role]

  const semanticsOf = (t: TableRead) => {
    const columnIds = new Set((t.columns ?? []).map((c) => c.id))
    return (list.data ?? []).filter((s) => (s.target_type === 'table' && s.target_id === t.id) || (s.target_type === 'column' && columnIds.has(s.target_id)))
  }

  return (
    <div className="cb-page">
      <Panel title="语义与别名" actions={<DatasourcePicker value={ds} onChange={setDs} />}>
        <table className="cb-data cb-data-fixed">
          <thead>
            <tr><th style={{ width: '26%' }}>表</th><th>说明</th><th style={{ width: 90 }}>字段数</th><th style={{ width: 120 }}>已设语义</th><th style={{ width: 110 }} /></tr>
          </thead>
          <tbody>
            {(tables.data ?? []).map((t) => {
              const mine = semanticsOf(t)
              const pending = mine.filter((s) => s.scope === 'personal' && s.status === 'private')
              return (
                <tr key={t.id}>
                  <td title={`编辑 ${t.name} 的别名`}>
                    <button type="button" className="cb-link" aria-label={`编辑 ${t.name} 的别名`} onClick={() => setEditing(t)}>{t.name}</button>
                  </td>
                  <td title={t.comment || t.description}>{t.comment || t.description || <span className="cb-muted">—</span>}</td>
                  <td className="cb-num">{(t.columns ?? []).length}</td>
                  <td className="cb-num">{mine.length ? `${mine.length} 条` : <span className="cb-muted">—</span>}</td>
                  <td style={{ textAlign: 'right' }}>
                    {role !== 'admin' && pending.length ? (
                      <Button size="sm" variant="ghost" onClick={() => pending.forEach((s) => promote.mutate({ kind: 'semantic', ref_id: s.id }))}>申请提升</Button>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {ds == null ? <div className="cb-muted">先在右上角选择数据源。</div> : null}
        {ds != null && tables.data && !tables.data.length ? <div className="cb-muted">这个数据源还没有可见的表。</div> : null}
      </Panel>
      {editing && ds != null ? (
        <SemanticDialog
          key={editing.id}
          datasourceId={ds}
          table={editing}
          scope={scope}
          semantics={list.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={(n) => {
            toast({ tone: 'success', title: '语义已保存', message: `${n} 条已更新` })
            void qc.invalidateQueries({ queryKey: ['semantic', ds] })
            setEditing(null)
          }}
        />
      ) : null}
    </div>
  )
}

function SemanticDialog(props: {
  datasourceId: number
  table: TableRead
  scope: ScopeLevel
  semantics: SemanticView[]
  onClose: () => void
  onSaved: (count: number) => void
}) {
  const { table, scope } = props
  const base = useMemo(() => buildRows(table, scope, props.semantics), [table, scope, props.semantics])
  const [edits, setEdits] = useState<Record<string, { alias?: string; description?: string }>>({})
  const rows = base.map((r) => ({ ...r, ...edits[r.key] }))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { save, remove } = planChanges(rows)
  const changed = rows.some((r) => edits[r.key])

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      for (const r of save) {
        const body = { datasource_id: props.datasourceId, target_type: r.targetType, target_id: r.targetId, aliases: splitAliases(r.alias), description: r.description.trim(), enum_mapping: {}, scope }
        if (r.existing) await api('PUT', `/api/semantic/${r.existing.id}`, { body })
        else await api('POST', '/api/semantic', { body })
      }
      for (const r of remove) if (r.existing) await api('DELETE', `/api/semantic/${r.existing.id}`)
      props.onSaved(save.length + remove.length)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '保存失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  const edit = (key: string, patch: { alias?: string; description?: string }) => setEdits((cur) => ({ ...cur, [key]: { ...cur[key], ...patch } }))

  return (
    <Dialog title={`编辑${SCOPE_NAME[scope]}别名 · ${table.name}`} size="xl" onClose={props.onClose}>
      <div className="cb-tree-scroll">
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr><th style={{ width: '22%' }}>字段名</th><th style={{ width: 110 }}>类型</th><th style={{ width: '30%' }}>别名</th><th>说明</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td className="cb-mono" title={r.name}>{r.name}</td>
                  <td><Badge mono>{r.type}</Badge></td>
                  <td>
                    <input className="cb-textarea" style={{ minHeight: 'var(--control-sm)', height: 'var(--control-sm)' }} aria-label={`${r.name} 别名`} placeholder="用顿号分隔" value={r.alias} onChange={(e) => edit(r.key, { alias: e.target.value })} />
                    {r.inherited.map((s) => (s.aliases ?? []).length ? <div key={s.id} className="cb-inherited">{SCOPE_NAME[s.scope]}：{(s.aliases ?? []).join('、')}</div> : null)}
                  </td>
                  <td>
                    <input className="cb-textarea" style={{ minHeight: 'var(--control-sm)', height: 'var(--control-sm)' }} aria-label={`${r.name} 说明`} value={r.description} onChange={(e) => edit(r.key, { description: e.target.value })} />
                    {r.inherited.map((s) => s.description ? <div key={s.id} className="cb-inherited" title={s.description}>{SCOPE_NAME[s.scope]}：{s.description}</div> : null)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      {error ? <Callout tone="danger">{error}</Callout> : null}
      <DialogFooter left={<span className="cb-muted">灰字是其他级别已生效的定义（只读）；这里填写的{SCOPE_NAME[scope]}定义优先。只保存填写了内容的行，清空会删除该条。</span>}>
        <Button variant="ghost" onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={busy || !changed} onClick={() => void submit()}>保存语义</Button>
      </DialogFooter>
    </Dialog>
  )
}
