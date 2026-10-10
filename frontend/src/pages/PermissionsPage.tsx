import { useMemo, useState } from 'react'
import { useGrants, useSaveGrants, useTables } from '../api/hooks'
import type { TableRead } from '../api/types'
import { Badge, Button, Callout, TextField } from '../components/basic'
import { Check } from '../components/Check'
import { DatasourcePicker } from '../components/DatasourcePicker'
import { UnsavedDialog } from '../components/Dialog'
import { Icon } from '../components/Icon'
import { EmptyState, ListStatus } from '../components/ListStates'
import { HeaderActions } from '../components/PageHeader'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { buildGrid, gridToMemberPayload, memberTableState, setMemberColumn, setMemberTable, type Grid } from '../lib/grid'
import { useUnsavedGuard } from '../state/unsaved'

/** 团队负责人：只在管理员授权给本团队的表和字段里，决定员工可见哪些。 */
export function PermissionsPage(props: { teamId: number | null }) {
  const toast = useToast()
  const [ds, setDs] = useState<number | null>(null)
  const [nextDs, setNextDs] = useState<number | null>(null)
  const tables = useTables(ds)
  const grants = useGrants(ds, props.teamId)
  const save = useSaveGrants(ds, props.teamId)
  const base = useMemo(() => buildGrid(tables.data ?? [], grants.data), [tables.data, grants.data])
  const key = `${ds}:${props.teamId}:${grants.dataUpdatedAt}`
  const [edits, setEdits] = useState<{ key: string; grid: Grid } | null>(null)
  const grid = edits?.key === key ? edits.grid : base
  const dirty = edits?.key === key
  const [open, setOpen] = useState<Set<number>>(new Set())
  const [q, setQ] = useState('')
  const [hiddenOnly, setHiddenOnly] = useState(false)

  const update = (fn: (g: Grid) => void) => {
    const next = structuredClone(grid)
    fn(next)
    setEdits({ key, grid: next })
  }
  const toggleOpen = (id: number) =>
    setOpen((cur) => {
      const next = new Set(cur)
      if (!next.delete(id)) next.add(id)
      return next
    })
  const persist = async (): Promise<boolean> => {
    if (ds == null || props.teamId == null) return false
    try {
      await save.mutateAsync({ mode: 'members', tables: gridToMemberPayload(grid) })
      setEdits(null)
      toast({ tone: 'success', title: '员工可见范围已保存' })
      return true
    } catch (e) {
      toast({ tone: 'danger', title: '保存失败', message: e instanceof ApiError ? describeError(e.code) : undefined })
      return false
    }
  }
  useUnsavedGuard(dirty, persist)

  const changeDs = (id: number | null) => {
    if (id === ds) return
    if (dirty) setNextDs(id)
    else {
      setDs(id)
      setOpen(new Set())
    }
  }
  const leave = () => {
    setEdits(null)
    setOpen(new Set())
    setDs(nextDs)
    setNextDs(null)
  }

  const needle = q.trim().toLowerCase()
  const columnsOf = (t: TableRead) => (t.columns ?? []).filter((c) => grid[t.id]?.columns[c.id]?.granted)
  const hidden = (t: TableRead, colId: number) => {
    const ts = grid[t.id]
    return !(ts?.member_visible && ts.columns[colId]?.member_visible)
  }
  const rows = (tables.data ?? [])
    .filter((t: TableRead) => grid[t.id]?.granted)
    .map((t) => {
      const ts = grid[t.id]
      const nameHit = !needle || t.name.toLowerCase().includes(needle)
      let cols = columnsOf(t)
      if (!nameHit) cols = cols.filter((c) => c.name.toLowerCase().includes(needle))
      if (hiddenOnly) cols = cols.filter((c) => hidden(t, c.id))
      const keep = ts && (nameHit || cols.length > 0) && (!hiddenOnly || cols.length > 0 || memberTableState(ts) !== 'on')
      return keep ? { t, cols, forced: !!needle && !nameHit } : null
    })
    .filter((r): r is { t: TableRead; cols: ReturnType<typeof columnsOf>; forced: boolean } => r != null)
  const grantedCount = (tables.data ?? []).filter((t: TableRead) => grid[t.id]?.granted).length
  const allOpen = rows.length > 0 && rows.every((r) => open.has(r.t.id))
  const toggleAll = () => setOpen(allOpen ? new Set() : new Set(rows.map((r) => r.t.id)))
  const filtered = !!needle || hiddenOnly
  const clear = () => {
    setQ('')
    setHiddenOnly(false)
  }

  return (
    <div className="cb-page">
      <HeaderActions>
        <DatasourcePicker value={ds} onChange={changeDs} />
        {dirty ? <Badge tone="warning">有未保存的修改</Badge> : null}
        <Button variant="primary" disabled={!dirty || save.isPending || ds == null || props.teamId == null} onClick={() => void persist()}>
          保存
        </Button>
      </HeaderActions>
      <Callout tone="info">这里只显示管理员授权给本团队的表和字段。取消勾选后，员工在提问和手改 SQL 时都无法访问，会被拦截。</Callout>
      <div className="cb-filters">
        <div className="cb-search">
          <TextField aria-label="筛选表或字段" placeholder="按表名或字段名筛选" icon="search" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Button variant="ghost" disabled={!rows.length} onClick={toggleAll}>
          {allOpen ? '全部收起' : '全部展开'}
        </Button>
        <label className="cb-pm-only">
          <input type="checkbox" checked={hiddenOnly} onChange={(e) => setHiddenOnly(e.target.checked)} />
          只看员工不可见
        </label>
      </div>
      <section className="cb-panel cb-pm-tree" aria-label="员工可见范围">
        <div className="cb-pm-head" role="row">
          <span>表 / 字段名</span>
          <span>类型</span>
          <span>员工可见</span>
        </div>
        {ds == null ? (
          <EmptyState icon="database" title="先选择数据源">在右上角选择要设置的数据源。</EmptyState>
        ) : (
          <ListStatus
            loading={tables.isLoading || grants.isLoading}
            error={tables.error ?? grants.error}
            onRetry={() => {
              void tables.refetch()
              void grants.refetch()
            }}
            what="员工可见范围"
            count={rows.length}
            filtered={filtered && grantedCount > 0}
            query={needle ? q.trim() : undefined}
            onClear={clear}
            empty={<EmptyState icon="lock" title="还没有可设置的表">管理员还没有把这个数据源授权给本团队，授权后可以在这里决定员工可见哪些字段。</EmptyState>}
          >
            {rows.map(({ t, cols, forced }) => {
              const ts = grid[t.id]
              if (!ts) return null
              const expanded = forced || open.has(t.id)
              return (
                <div key={t.id} className="cb-pm-table">
                  <div className="cb-pm-row">
                    <button type="button" className="cb-pm-toggle" aria-expanded={expanded} onClick={() => toggleOpen(t.id)}>
                      <Icon name="chevronDown" size="sm" />
                      <span className="cb-m cb-pm-name">{t.name}</span>
                      <Badge>{columnsOf(t).length} 个字段</Badge>
                    </button>
                    <span />
                    <Check state={memberTableState(ts)} label={`员工可见表 ${t.name}`} onChange={(on) => update((g) => setMemberTable(g, t.id, on))} />
                  </div>
                  {expanded
                    ? cols.map((c) => {
                        const cs = ts.columns[c.id]
                        if (!cs) return null
                        return (
                          <div key={c.id} className="cb-pm-row cb-pm-col">
                            <span className="cb-m">{c.name}</span>
                            <span><Badge mono>{c.data_type}</Badge></span>
                            <Check state={ts.member_visible && cs.member_visible ? 'on' : 'off'} label={`员工可见字段 ${t.name}.${c.name}`} onChange={(on) => update((g) => setMemberColumn(g, t.id, c.id, on))} />
                          </div>
                        )
                      })
                    : null}
                </div>
              )
            })}
          </ListStatus>
        )}
      </section>
      {nextDs != null ? (
        <UnsavedDialog
          onStay={() => setNextDs(null)}
          onDiscard={leave}
          saving={save.isPending}
          onSave={() => void persist().then((ok) => ok && leave())}
        />
      ) : null}
    </div>
  )
}
