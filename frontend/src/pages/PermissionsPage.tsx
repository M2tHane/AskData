import { useMemo, useState } from 'react'
import { useGrants, useSaveGrants, useTables } from '../api/hooks'
import type { TableRead } from '../api/types'
import { Badge, Button, Callout, Panel, TextField } from '../components/basic'
import { Check } from '../components/Check'
import { DatasourcePicker } from '../components/DatasourcePicker'
import { Icon } from '../components/Icon'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { buildGrid, gridToMemberPayload, memberTableState, setMemberColumn, setMemberTable, type Grid } from '../lib/grid'

/** 团队负责人：只在管理员授权给本团队的表和字段里，决定员工可见哪些。 */
export function PermissionsPage(props: { teamId: number | null }) {
  const toast = useToast()
  const [ds, setDs] = useState<number | null>(null)
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
  const submit = () =>
    save.mutate(
      { mode: 'members', tables: gridToMemberPayload(grid) },
      {
        onSuccess: () => {
          setEdits(null)
          toast({ tone: 'success', title: '员工可见范围已保存' })
        },
        onError: (e) => toast({ tone: 'danger', title: '保存失败', message: e instanceof ApiError ? describeError(e.code) : undefined }),
      },
    )

  const list = (tables.data ?? []).filter((t: TableRead) => grid[t.id]?.granted && t.name.toLowerCase().includes(q.trim().toLowerCase()))

  return (
    <div className="cb-page">
      <Panel
        title="员工可见范围"
        actions={
          <div className="cb-row">
            <DatasourcePicker value={ds} onChange={setDs} />
            {dirty ? <Badge tone="warning" icon="alert">有未保存的修改</Badge> : null}
            <Button variant="primary" disabled={!dirty || save.isPending || ds == null || props.teamId == null} onClick={submit}>保存</Button>
          </div>
        }
      >
        <Callout tone="info">
          这里只显示管理员授权给本团队的表和字段。取消勾选后，员工在提问和手改 SQL 时都无法访问，会被拦截。
        </Callout>
        <div className="cb-toolbar">
          <TextField aria-label="筛选表" placeholder="按表名筛选" icon="search" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="cb-tree-scroll">
          <div className="cb-tree-head" role="row">
            <span>表 / 字段名</span>
            <span>类型</span>
            <span>员工可见</span>
          </div>
          {list.map((t) => {
            const ts = grid[t.id]
            if (!ts) return null
            const expanded = open.has(t.id)
            const granted = (t.columns ?? []).filter((c) => ts.columns[c.id]?.granted)
            return (
              <div key={t.id} className="cb-tree-table">
                <div className="cb-tree-row">
                  <button type="button" className="cb-tree-toggle" aria-expanded={expanded} onClick={() => toggleOpen(t.id)}>
                    <Icon name="chevronDown" size="sm" />
                    <span className="cb-mono">{t.name}</span>
                    <Badge>{granted.length} 个字段</Badge>
                  </button>
                  <span />
                  <Check state={memberTableState(ts)} label={`员工可见表 ${t.name}`} onChange={(on) => update((g) => setMemberTable(g, t.id, on))} />
                </div>
                {expanded
                  ? granted.map((c) => {
                      const cs = ts.columns[c.id]
                      if (!cs) return null
                      return (
                        <div key={c.id} className="cb-tree-row cb-tree-col">
                          <span className="cb-mono">{c.name}</span>
                          <span><Badge mono>{c.data_type}</Badge></span>
                          <Check state={ts.member_visible && cs.member_visible ? 'on' : 'off'} label={`员工可见字段 ${t.name}.${c.name}`} onChange={(on) => update((g) => setMemberColumn(g, t.id, c.id, on))} />
                        </div>
                      )
                    })
                  : null}
              </div>
            )
          })}
          {ds == null ? <div className="cb-muted" style={{ padding: 'var(--space-4)' }}>先在右上角选择数据源。</div> : null}
          {ds != null && tables.data && !list.length ? <div className="cb-muted" style={{ padding: 'var(--space-4)' }}>{q ? '没有匹配的表。' : '管理员还没有把这个数据源授权给本团队。'}</div> : null}
        </div>
      </Panel>
    </div>
  )
}
