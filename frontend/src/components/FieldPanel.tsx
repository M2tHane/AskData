import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useScope, useSemantics, useTables } from '../api/hooks'
import { buildFields, type FieldInfo, type TableInfo } from '../lib/fields'
import { ScopeTag } from './basic'
import { Icon } from './Icon'
import { LoadError, SkeletonRows } from './ListStates'
import { Pager } from './Pager'

const FIELDS_PER_PAGE = 15

/** 表下拉：先是搜索框（表名或中文名），每项三列：表名 / 中文名 / 字段数。 */
function TablePicker(props: { tables: TableInfo[]; value: TableInfo | undefined; onChange: (key: string) => void }) {
  const [rect, setRect] = useState<{ left: number; top: number; width: number } | null>(null)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const trigger = useRef<HTMLButtonElement>(null)
  const pop = useRef<HTMLDivElement>(null)
  const needle = q.trim().toLowerCase()
  const shown = props.tables.filter((t) => !needle || t.name.toLowerCase().includes(needle) || t.label.toLowerCase().includes(needle))
  const close = () => {
    setRect(null)
    trigger.current?.focus()
  }
  useEffect(() => {
    if (!rect) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (!pop.current?.contains(t) && !trigger.current?.contains(t)) setRect(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [rect])
  const pick = (t: TableInfo) => {
    props.onChange(t.key)
    close()
  }
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="cb-sel-trigger cb-dict-trigger"
        aria-haspopup="listbox"
        aria-expanded={rect != null}
        aria-label="选择表"
        onClick={() => {
          if (rect) return close()
          const r = trigger.current?.getBoundingClientRect()
          // 面板至少 360px，靠右时向左展开，不出窗口。
          if (r) {
            const width = Math.max(r.width, 360)
            setRect({ left: Math.max(8, Math.min(r.left, window.innerWidth - width - 8)), top: r.bottom + 4, width })
          }
          setQ('')
          setActive(Math.max(0, props.tables.findIndex((t) => t.key === props.value?.key)))
        }}
      >
        <span>
          {props.value ? (
            <>
              {props.value.name}
              {props.value.label ? <span className="cb-faint"> · {props.value.label}</span> : null}
            </>
          ) : (
            '选择表'
          )}
        </span>
        <Icon name="chevronDown" />
      </button>
      {rect
        ? createPortal(
            <div ref={pop} className="cb-sel-pop cb-dict-pop" style={{ left: rect.left, top: rect.top, width: rect.width }}>
              <div className="cb-menu-search">
                <Icon name="search" />
                <input
                  type="search"
                  autoFocus
                  aria-label="按表名或中文名搜索"
                  placeholder="按表名或中文名搜索"
                  value={q}
                  onChange={(e) => {
                    setQ(e.target.value)
                    setActive(0)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                      e.preventDefault()
                      e.stopPropagation()
                      close()
                    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                      e.preventDefault()
                      setActive((i) => Math.max(0, Math.min(shown.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1))))
                    } else if (e.key === 'Enter') {
                      e.preventDefault()
                      const t = shown[active]
                      if (t) pick(t)
                    }
                  }}
                />
              </div>
              <ul role="listbox" aria-label="可见的表" className="cb-sel-list">
                {shown.length === 0 ? <li className="cb-sel-empty">没有匹配的表</li> : null}
                {shown.map((t, i) => (
                  <li
                    key={t.key}
                    role="option"
                    aria-selected={t.key === props.value?.key}
                    data-active={i === active || undefined}
                    className="cb-dict-option"
                    onMouseEnter={() => setActive(i)}
                    onMouseDown={(e) => {
                      e.preventDefault()
                      pick(t)
                    }}
                  >
                    <span className="cb-mono">{t.name}</span>
                    <small>{t.label}</small>
                    <em>{t.fields.length} 个字段</em>
                  </li>
                ))}
              </ul>
            </div>,
            document.body,
          )
        : null}
    </>
  )
}

/**
 * 对话页右侧的「可用字段」：表下拉 → 字段名 / 类型 / 别名 → 点一行看说明。
 * 只列当前用户可见的表和字段；字段超过 15 个时分页，翻页后选中该页第一行。
 */
export function FieldPanel(props: { datasourceId: number; onInsert: (text: string) => void }) {
  const scope = useScope(props.datasourceId)
  const tables = useTables(props.datasourceId)
  const semantics = useSemantics(props.datasourceId)
  const all = useMemo(() => buildFields(scope.data, tables.data, semantics.data), [scope.data, tables.data, semantics.data])
  const [tableKey, setTableKey] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [picked, setPicked] = useState<string | null>(null)
  const table = all.find((t) => t.key === tableKey) ?? all[0]
  const fields = table?.fields ?? []
  const last = Math.max(1, Math.ceil(fields.length / FIELDS_PER_PAGE))
  const current = Math.min(page, last)
  const rows = fields.slice((current - 1) * FIELDS_PER_PAGE, current * FIELDS_PER_PAGE)
  const field: FieldInfo | undefined = rows.find((f) => f.name === picked) ?? rows[0]

  if (scope.isError) return <LoadError error={scope.error} what="可用字段" onRetry={() => void scope.refetch()} />
  if (scope.isPending) return <SkeletonRows rows={8} cols={3} />
  if (all.length === 0)
    return (
      <div className="cb-state">
        <b>没有可用的表</b>
        <p>你在这个数据源上还没有可见的表，联系团队负责人开通。</p>
      </div>
    )

  return (
    <>
      <div className="cb-dict-pick">
        <TablePicker
          tables={all}
          value={table}
          onChange={(key) => {
            setTableKey(key)
            setPage(1)
            setPicked(null)
          }}
        />
        <span className="cb-field-hint">只显示你有权限查看的 {all.length} 张表</span>
      </div>
      <div className="cb-dict-list">
        <table className="cb-data cb-dict-table" aria-label={`${table?.name ?? ''} 的字段`}>
          <thead>
            <tr>
              <th>字段名</th>
              <th>类型</th>
              <th>别名</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <tr
                key={f.name}
                aria-selected={f.name === field?.name}
                tabIndex={0}
                onClick={() => setPicked(f.name)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setPicked(f.name)
                  }
                }}
              >
                <td className="cb-mono" title={f.name}>
                  {f.name}
                </td>
                <td className="cb-mono cb-faint" title={f.type}>
                  {f.type}
                </td>
                <td title={f.aliases.join('、')}>{f.aliases[0] ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager
        compact
        label="字段分页"
        page={current}
        pageSize={FIELDS_PER_PAGE}
        total={fields.length}
        onPage={(p) => {
          setPage(p)
          setPicked(null)
        }}
      />
      {field && table ? (
        <div className="cb-fd" aria-live="polite">
          <div className="cb-row">
            <b className="cb-mono">
              {table.name}.{field.name}
            </b>
            {field.level ? <ScopeTag level={field.level} /> : null}
          </div>
          {field.description ? <p>{field.description}</p> : <p className="cb-faint">还没有说明。</p>}
          {Object.keys(field.enums).length ? (
            <div className="cb-fd-enum">
              {Object.entries(field.enums).map(([k, v]) => (
                <span key={k}>
                  <span className="cb-mono">{k}</span> = {v}
                </span>
              ))}
            </div>
          ) : null}
          <button type="button" className="cb-btn cb-btn-ghost cb-btn-sm cb-fd-insert" onClick={() => props.onInsert(field.aliases[0] ?? field.name)}>
            <Icon name="plus" size="sm" />
            插入到问题
          </button>
        </div>
      ) : null}
    </>
  )
}
