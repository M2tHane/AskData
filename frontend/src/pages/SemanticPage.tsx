import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useSemantics, useTables } from '../api/hooks'
import type { Role, ScopeLevel, SemanticView, TableRead } from '../api/types'
import { Badge, Button, ScopeTag, TextField } from '../components/basic'
import { ConfirmDialog, UnsavedDialog } from '../components/Dialog'
import { DatasourcePicker } from '../components/DatasourcePicker'
import { Icon } from '../components/Icon'
import { HeaderActions } from '../components/PageHeader'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { LEVEL_SHORT, OWN_SCOPE, SCOPE_NAME } from '../lib/scope'
import { draftBody, levelView, planStep, sameDraft, sourceOf, splitAliases, toDraft, type EnumMap, type LevelView, type SemanticDraft } from '../lib/semanticLevels'
import { useSchemaChanges } from '../state/schemaChanges'
import { useUnsavedGuard } from '../state/unsaved'

export { splitAliases }

/* 下面三项是旧的「批量编辑对话框」用的整理函数，保留导出以兼容现有测试；页面本身已改用 lib/semanticLevels。 */
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

const keyOf = (type: 'table' | 'column', id: number) => `${type}:${id}`

const FOOTER_NOTE: Record<Role, string> = {
  member: '你改的只对你自己生效。改过的字段沿用团队的设置，团队没设的沿用平台默认。「恢复」回到上一级当前的值。',
  leader: '你改的对本团队所有成员生效，优先于平台默认。「恢复为平台」回到平台当前的值。',
  admin: '平台级是所有人的默认，只有管理员能改；团队负责人和员工可以各自覆盖。',
}

export function SemanticPage({ role }: { role: Role }) {
  const toast = useToast()
  const qc = useQueryClient()
  const level = OWN_SCOPE[role]
  const [params] = useSearchParams()
  const [ds, setDs] = useState<number | null>(() => Number(params.get('ds')) || null)
  const tables = useTables(ds)
  const list = useSemantics(ds)
  const changes = useSchemaChanges((s) => (ds == null ? undefined : s.byDatasource[ds]))
  const [picked, setPicked] = useState<string | null>(params.get('table'))
  const [q, setQ] = useState('')
  const [edits, setEdits] = useState<Record<string, SemanticDraft>>({})
  const [busy, setBusy] = useState(false)
  const [leaving, setLeaving] = useState<(() => void) | null>(null)
  const [restoring, setRestoring] = useState<{ key: string; name: string; view: LevelView } | null>(null)

  const all = useMemo(() => tables.data ?? [], [tables.data])
  const needle = q.trim().toLowerCase()
  const shown = all.filter((t) => !needle || `${t.name} ${t.comment} ${t.description}`.toLowerCase().includes(needle) || (t.columns ?? []).some((c) => c.name.toLowerCase().includes(needle)))
  const table = all.find((t) => t.name === picked) ?? all[0]

  /** 每个目标（表或字段）的全部可见定义。 */
  const byTarget = useMemo(() => {
    const m = new Map<string, SemanticView[]>()
    for (const s of list.data ?? []) {
      if (s.target_type === 'enum') continue
      const k = keyOf(s.target_type, s.target_id)
      m.set(k, [...(m.get(k) ?? []), s])
    }
    return m
  }, [list.data])
  const viewOf = useCallback((key: string) => levelView(byTarget.get(key) ?? [], level), [byTarget, level])
  const draftOf = (key: string) => edits[key] ?? toDraft(viewOf(key).base)

  const changedKeys = Object.keys(edits).filter((k) => !sameDraft(edits[k]!, toDraft(viewOf(k).base)))
  const dirty = changedKeys.length > 0

  const save = async (): Promise<boolean> => {
    if (ds == null) return false
    setBusy(true)
    let done = 0
    try {
      for (const key of changedKeys) {
        const [type, id] = key.split(':') as ['table' | 'column', string]
        const view = viewOf(key)
        const step = planStep(edits[key]!, view)
        if (step.op === 'skip') continue
        if (step.op === 'delete') await api('DELETE', `/api/semantic/${step.id}`)
        else {
          const body = { datasource_id: ds, target_type: type, target_id: Number(id), scope: level, ...draftBody(edits[key]!, view.own?.enum_mapping as EnumMap | undefined) }
          if (step.op === 'update') await api('PUT', `/api/semantic/${step.id}`, { body })
          else await api('POST', '/api/semantic', { body })
        }
        done++
      }
      await qc.invalidateQueries({ queryKey: ['semantic', ds] })
      setEdits({})
      toast({ tone: 'success', title: '语义已保存', message: `${done} 处已更新` })
      return true
    } catch (e) {
      await qc.invalidateQueries({ queryKey: ['semantic', ds] })
      toast({ tone: 'danger', title: '保存失败', message: e instanceof ApiError ? describeError(e.code) : '请稍后重试。已保存的部分不会丢失。' })
      return false
    } finally {
      setBusy(false)
    }
  }
  const saveRef = useRef(save)
  useEffect(() => {
    saveRef.current = save
  })
  const guardSave = useCallback(() => saveRef.current(), [])
  useUnsavedGuard(dirty, guardSave)

  const guard = (go: () => void) => (dirty ? setLeaving(() => go) : go())

  const restore = async () => {
    if (!restoring?.view.own) return
    setBusy(true)
    try {
      await api('DELETE', `/api/semantic/${restoring.view.own.id}`)
      await qc.invalidateQueries({ queryKey: ['semantic', ds] })
      setEdits((cur) => {
        const next = { ...cur }
        delete next[restoring.key]
        return next
      })
      const to = restoring.view.upper ? LEVEL_SHORT[restoring.view.upper.scope] : '平台'
      toast({ tone: 'success', title: `「${restoring.name}」已恢复为${to}`, message: `${SCOPE_NAME[level]}的修改已删除。` })
    } catch (e) {
      toast({ tone: 'danger', title: '恢复失败', message: e instanceof ApiError ? describeError(e.code) : '请稍后重试。' })
    } finally {
      setBusy(false)
      setRestoring(null)
    }
  }

  const changeCount = (t: TableRead) => (changes?.changes ?? []).filter((c) => c.table === t.name).length
  const filled = (t: TableRead) => [keyOf('table', t.id), ...(t.columns ?? []).map((c) => keyOf('column', c.id))].filter((k) => viewOf(k).base).length

  const setDraft = (key: string, patch: Partial<SemanticDraft>) => setEdits((cur) => ({ ...cur, [key]: { ...(cur[key] ?? toDraft(viewOf(key).base)), ...patch } }))
  const ask = (key: string, name: string) => setRestoring({ key, name, view: viewOf(key) })

  const sourceCell = (key: string, name: string) => {
    const src = sourceOf(viewOf(key))
    if (src.kind === 'own') {
      const upper = viewOf(key).upper
      const to = upper ? LEVEL_SHORT[upper.scope] : ''
      return (
        <>
          <ScopeTag level={level} />
          {upper ? (
            <button type="button" className="cb-link cb-sem-restore" aria-label={`${name} 恢复为${to}`} onClick={() => ask(key, name)}>
              恢复为{to}
            </button>
          ) : null}
        </>
      )
    }
    return <span>{src.kind === 'inherit' ? `沿用${LEVEL_SHORT[src.from]}` : '未设置'}</span>
  }

  const tkey = table ? keyOf('table', table.id) : ''
  const tdraft = table ? draftOf(tkey) : null

  return (
    <div className="cb-page">
      <HeaderActions>
        <DatasourcePicker value={ds} onChange={(id) => guard(() => { setEdits({}); setPicked(null); setDs(id) })} />
      </HeaderActions>
      <div className="cb-sem-grid">
        <ListCard
          label="表列表"
          toolbar={
            <div className="cb-sem-search">
              <TextField aria-label="搜索表或字段" placeholder="搜索表或字段" icon="search" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          }
        >
          <ListStatus
            loading={ds == null || tables.isPending}
            error={tables.error}
            onRetry={() => void tables.refetch()}
            what="表"
            count={shown.length}
            filtered={!!needle}
            query={needle ? q.trim() : undefined}
            onClear={() => setQ('')}
            cols={2}
            empty={<EmptyState icon="table" title="这个数据源还没有可见的表">同步数据源，或请管理员授予表的权限后，这里会列出表。</EmptyState>}
          >
            <div className="cb-sem-listhead"><span>表</span><span>已设语义 / 字段数</span></div>
            <ul className="cb-sem-tables">
              {shown.map((t) => {
                const n = changeCount(t)
                return (
                  <li key={t.id}>
                    <button type="button" className="cb-sem-table" aria-current={table?.id === t.id ? 'true' : undefined} onClick={() => t.id !== table?.id && guard(() => { setEdits({}); setPicked(t.name) })}>
                      <span className="cb-sem-tname">
                        <b title={t.name}>{t.name}</b>
                        <span className="cb-sub" title={t.comment || t.description}>{t.comment || t.description || '—'}</span>
                      </span>
                      {n ? <Badge tone="warning">{n} 处变化</Badge> : null}
                      <span className="cb-r cb-sem-count">{filled(t)}/{(t.columns ?? []).length}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </ListStatus>
        </ListCard>

        <ListCard
          label="字段语义"
          toolbar={
            table && tdraft ? (
              <div className="cb-sem-head">
                <span className="cb-sem-ic" aria-hidden="true"><Icon name="table" /></span>
                <span className="cb-sem-tname">
                  <b title={table.name}>{table.name}</b>
                  <span className="cb-sub">{table.schema_name} · {(table.columns ?? []).length} 个字段</span>
                </span>
                <label className="cb-sem-lab">
                  别名
                  <input className="cb-sem-in" value={tdraft.alias} placeholder="用顿号分隔" onChange={(e) => setDraft(tkey, { alias: e.target.value })} />
                </label>
                <label className="cb-sem-lab cb-sem-lab-grow">
                  说明
                  <input className="cb-sem-in" value={tdraft.description} placeholder="这张表记录什么" onChange={(e) => setDraft(tkey, { description: e.target.value })} />
                </label>
                <span className="cb-sem-end">{sourceCell(tkey, table.name)}</span>
              </div>
            ) : (
              <span />
            )
          }
          pager={
            table ? (
              <div className="cb-sem-foot">
                <Icon name="info" />
                <span className="cb-sem-note">{FOOTER_NOTE[role]}</span>
                <Button variant="ghost" disabled={!dirty || busy} onClick={() => setEdits({})}>放弃修改</Button>
                <Button variant="primary" disabled={!dirty || busy} onClick={() => void save()}>{busy ? '保存中…' : '保存'}</Button>
              </div>
            ) : undefined
          }
        >
          {!table ? (
            <EmptyState icon="table" title="选一张表开始">在左侧选择表，给它和它的字段设置别名与说明。</EmptyState>
          ) : (
            <table className="cb-data cb-data-fixed cb-sem-fields">
              <thead>
                <tr>
                  <th style={{ width: '27%' }}>字段 · 类型 · 来源</th>
                  <th style={{ width: '28%' }}>别名</th>
                  <th>说明</th>
                  <th style={{ width: 112 }}>枚举值映射</th>
                </tr>
              </thead>
              <tbody>
                {(table.columns ?? []).map((c) => {
                  const key = keyOf('column', c.id)
                  const d = draftOf(key)
                  const view = viewOf(key)
                  return (
                    <FieldRows
                      key={c.id}
                      name={c.name}
                      type={c.data_type}
                      draft={d}
                      overridden={!!view.own}
                      source={sourceCell(key, c.name)}
                      enumSource={view.own ? `${SCOPE_NAME[level]}已修改` : view.upper ? `沿用${LEVEL_SHORT[view.upper.scope]}` : ''}
                      onChange={(patch) => setDraft(key, patch)}
                    />
                  )
                })}
              </tbody>
            </table>
          )}
        </ListCard>
      </div>

      {leaving ? (
        <UnsavedDialog
          saving={busy}
          onStay={() => setLeaving(null)}
          onDiscard={() => {
            const go = leaving
            setLeaving(null)
            go()
          }}
          onSave={() => {
            const go = leaving
            void save().then((ok) => {
              if (ok) {
                setLeaving(null)
                go()
              }
            })
          }}
        />
      ) : null}
      {restoring ? (
        <ConfirmDialog
          title={`恢复「${restoring.name}」`}
          confirmLabel={`恢复为${restoring.view.upper ? LEVEL_SHORT[restoring.view.upper.scope] : '平台'}`}
          busy={busy}
          onClose={() => setRestoring(null)}
          onConfirm={() => void restore()}
        >
          {SCOPE_NAME[level]}对这个字段的别名、说明和枚举值映射会被删除，改用{restoring.view.upper ? SCOPE_NAME[restoring.view.upper.scope] : '上一级'}当前的值。
        </ConfirmDialog>
      ) : null}
    </div>
  )
}

function FieldRows(props: {
  name: string
  type: string
  draft: SemanticDraft
  overridden: boolean
  source: ReactNode
  enumSource: string
  onChange: (patch: Partial<SemanticDraft>) => void
}) {
  const { name, draft, onChange } = props
  const setPair = (i: number, patch: Partial<{ key: string; value: string }>) => onChange({ enums: draft.enums.map((e, j) => (j === i ? { ...e, ...patch } : e)) })
  return (
    <>
      <tr className={props.overridden ? 'cb-sem-own' : undefined}>
        <td>
          <span className="cb-m cb-sem-fname" title={name}>{name}</span>
          <span className="cb-sub cb-sem-src">{props.type} · {props.source}</span>
        </td>
        <td><input className="cb-sem-in" aria-label={`${name} 别名`} value={draft.alias} placeholder="用顿号分隔" onChange={(e) => onChange({ alias: e.target.value })} /></td>
        <td><input className="cb-sem-in" aria-label={`${name} 说明`} value={draft.description} placeholder="这个字段是什么" onChange={(e) => onChange({ description: e.target.value })} /></td>
        <td>
          <label className="cb-sem-check">
            <input type="checkbox" checked={draft.enumOn} onChange={(e) => onChange({ enumOn: e.target.checked, enums: e.target.checked && !draft.enums.length ? [{ key: '', value: '' }] : draft.enums })} />
            枚举
          </label>
        </td>
      </tr>
      {draft.enumOn ? (
        <tr className={props.overridden ? 'cb-sem-own cb-sem-enumrow' : 'cb-sem-enumrow'}>
          <td colSpan={4}>
            <div className="cb-sem-enum">
              <div className="cb-sem-enum-head">
                <b>{name} 的取值含义</b>
                {props.enumSource ? <span className="cb-faint">{props.enumSource}</span> : null}
              </div>
              {draft.enums.map((e, i) => (
                <div key={i} className="cb-sem-pair">
                  <input className="cb-sem-in cb-m" aria-label={`${name} 取值 ${i + 1}`} placeholder="取值" value={e.key} onChange={(ev) => setPair(i, { key: ev.target.value })} />
                  <Icon name="chevronRight" />
                  <input className="cb-sem-in" aria-label={`${name} 含义 ${i + 1}`} placeholder="含义" value={e.value} onChange={(ev) => setPair(i, { value: ev.target.value })} />
                  <Button size="sm" variant="ghost" icon="x" aria-label={`删除 ${name} 的取值 ${i + 1}`} onClick={() => onChange({ enums: draft.enums.filter((_, j) => j !== i) })} />
                </div>
              ))}
              <Button size="sm" variant="ghost" icon="plus" onClick={() => onChange({ enums: [...draft.enums, { key: '', value: '' }] })}>添加一项</Button>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  )
}
