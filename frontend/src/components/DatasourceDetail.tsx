import { useMemo, useState } from 'react'
import { useSaveVisibility, useTables, useTeams, useVisibility } from '../api/hooks'
import type { DatasourceRead, TableRead } from '../api/types'
import { ApiError, describeError } from '../lib/errors'
import {
  fromRead,
  inheritColumn,
  inheritTable,
  isCustom,
  resetAll,
  resolve,
  sameState,
  setColumnTeams,
  setDatasourceTeams,
  setTableTeams,
  toWrite,
  type VState,
} from '../lib/visibility'
import { Badge, Button, Callout, TextField } from './basic'
import { Dialog, DialogFooter } from './Dialog'
import { Icon } from './Icon'
import { MultiSelect } from './MultiSelect'
import { ScopeChips } from './ScopeChips'
import { useToast } from './Toast'

/** 数据源详情（管理员）：先定数据源的可见团队，表和字段默认继承，只在例外处单独设置；保存后一次生效。 */
export function DatasourceDetail(props: { datasource: DatasourceRead; onClose: () => void }) {
  const toast = useToast()
  const ds = props.datasource
  const tables = useTables(ds.id)
  const teams = useTeams()
  const vis = useVisibility(ds.id)
  const save = useSaveVisibility(ds.id)
  const loadError = tables.error ?? vis.error
  const [open, setOpen] = useState<Set<number>>(new Set())
  const [q, setQ] = useState('')

  const base = useMemo(() => (vis.data ? fromRead(vis.data) : null), [vis.data])
  const [draft, setDraft] = useState<{ key: number; state: VState } | null>(null)
  const state = draft && draft.key === vis.dataUpdatedAt ? draft.state : base
  const dirty = !!state && !!base && !sameState(state, base)
  const edit = (fn: (s: VState) => VState) => state && setDraft({ key: vis.dataUpdatedAt, state: fn(state) })
  const eff = useMemo(() => (state ? resolve(state) : null), [state])

  const pool = (teams.data?.items ?? []).filter((t) => t.status === 'active').map((t) => ({ id: t.id, name: t.name }))
  const nameOf = (id: number) => pool.find((t) => t.id === id)?.name ?? `团队 ${id}`
  const summary = (ids: number[]) => (ids.length ? ids.map(nameOf).join('、') : '无团队可见')
  const dsPool = pool.filter((t) => state?.teams.includes(t.id))

  const toggleOpen = (id: number) =>
    setOpen((cur) => {
      const next = new Set(cur)
      if (!next.delete(id)) next.add(id)
      return next
    })
  const list = (tables.data ?? []).filter((t) => t.name.toLowerCase().includes(q.trim().toLowerCase()))
  const submit = () =>
    state &&
    save.mutate(toWrite(state), {
      onSuccess: () => {
        toast({ tone: 'success', title: '可见范围已保存' })
        props.onClose()
      },
    })

  return (
    <Dialog title={`${ds.name} · 可见范围`} size="xl" onClose={props.onClose}>
      <div className="cb-scope-bar">
        <span className="cb-scope-label">可见范围</span>
        <ScopeChips label="数据源可见范围" pool={pool} value={state?.teams ?? []} disabled={!state} onChange={(ids) => edit((s) => setDatasourceTeams(s, ids))} />
        {state && isCustom(state) ? <Badge tone="warning" icon="alert">自定义</Badge> : <Badge icon="check">全部继承</Badge>}
        {state && isCustom(state) ? (
          <Button size="sm" variant="ghost" onClick={() => edit(resetAll)}>全部恢复继承</Button>
        ) : null}
      </div>
      <Callout tone="info">
        表和字段默认继承这里的可见范围，新同步或新上传的表和字段也一样。展开表后可以单独调整某张表或某个字段，调整后数据源会显示为「自定义」。团队负责人在各自团队的范围内，再决定哪些对员工可见。修改在点「保存」后生效。
      </Callout>
      {ds.status === 'disabled' ? <Callout tone="warning">这个数据源已停用，暂时没人能查。可以先调整字段的可见范围，重新启用后生效。</Callout> : null}
      {!pool.length && teams.data ? <Callout tone="warning">还没有启用的团队，先到「团队与成员」添加团队。</Callout> : null}
      {loadError ? <Callout tone="danger" title="表结构加载失败">{describeError(loadError instanceof ApiError ? loadError.code : undefined)}</Callout> : null}
      {save.error instanceof ApiError ? <Callout tone="danger">{describeError(save.error.code)}</Callout> : null}
      <div className="cb-toolbar">
        <TextField aria-label="筛选表" placeholder="按表名筛选" icon="search" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="cb-tree-scroll">
        <div className="cb-tree-head" role="row">
          <span>表 / 字段名</span>
          <span>类型</span>
          <span>可见范围</span>
        </div>
        {state && eff
          ? list.map((t: TableRead) => {
              const tv = state.tables[t.id]
              if (!tv) return null
              const tableTeams = eff.tables[t.id] ?? []
              const expanded = open.has(t.id)
              return (
                <div key={t.id} className="cb-tree-table">
                  <div className="cb-tree-row">
                    <button type="button" className="cb-tree-toggle" aria-expanded={expanded} onClick={() => toggleOpen(t.id)}>
                      <Icon name="chevronDown" size="sm" />
                      <span className="cb-mono">{t.name}</span>
                      <Badge>{(t.columns ?? []).length} 个字段</Badge>
                    </button>
                    <span />
                    <span className="cb-scope-cell" title={summary(tableTeams)}>
                      <Badge tone={tv.custom ? 'warning' : 'neutral'}>{tv.custom ? '自定义' : '继承'}</Badge>
                      <span className="cb-ellipsis">{summary(tableTeams)}</span>
                    </span>
                  </div>
                  {expanded ? (
                    <>
                      <div className="cb-tree-scope">
                        <span className="cb-muted">这张表对</span>
                        <ScopeChips label={`表 ${t.name} 可见范围`} pool={dsPool} value={tableTeams} onChange={(ids) => edit((s) => setTableTeams(s, t.id, ids))} />
                        {tv.custom ? <Button size="sm" variant="ghost" onClick={() => edit((s) => inheritTable(s, t.id))}>恢复为继承</Button> : null}
                      </div>
                      {(t.columns ?? []).map((c) => {
                        const cv = tv.columns[c.id]
                        if (!cv) return null
                        const colTeams = eff.columns[c.id] ?? []
                        const colPool = pool.filter((p) => tableTeams.includes(p.id))
                        return (
                          <div key={c.id} className="cb-tree-row cb-tree-col">
                            <span className="cb-mono">
                              {c.name} {c.is_pk ? <Badge tone="info">主键</Badge> : null}
                            </span>
                            <span><Badge mono>{c.data_type}</Badge></span>
                            <span className="cb-scope-cell" title={summary(colTeams)}>
                              <Badge tone={cv.custom ? 'warning' : 'neutral'}>{cv.custom ? '自定义' : '继承'}</Badge>
                              <MultiSelect
                                variant="link"
                                ariaLabel={`字段 ${t.name}.${c.name} 可见范围`}
                                disabled={!colPool.length}
                                options={colPool.map((p) => ({ value: p.id, label: p.name }))}
                                value={colTeams}
                                onChange={(ids) => edit((s) => setColumnTeams(s, t.id, c.id, ids))}
                                footer={
                                  cv.custom ? (
                                    <Button size="sm" variant="ghost" onClick={() => edit((s) => inheritColumn(s, t.id, c.id))}>恢复为继承</Button>
                                  ) : (
                                    <span className="cb-muted">勾选后只对所选团队可见</span>
                                  )
                                }
                              >
                                <span className="cb-ellipsis">{summary(colTeams)}</span>
                              </MultiSelect>
                            </span>
                          </div>
                        )
                      })}
                    </>
                  ) : null}
                </div>
              )
            })
          : null}
        {tables.data && !list.length ? <div className="cb-muted" style={{ padding: 'var(--space-4)' }}>{q ? '没有匹配的表。' : '还没有元数据，关闭后点「同步」获取表结构。'}</div> : null}
      </div>
      <DialogFooter left={dirty ? <Badge tone="warning" icon="alert">有未保存的修改</Badge> : null}>
        <Button variant="ghost" onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={!dirty || save.isPending} onClick={submit}>保存</Button>
      </DialogFooter>
    </Dialog>
  )
}
