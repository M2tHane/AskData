import { useState } from 'react'
import { Icon } from './Icon'

export const PAGE_SIZE = 10

/** 页码列表：不超过 7 页全列出；更多时保留首尾和当前页附近，中间用省略号（null）。 */
export function pageItems(page: number, last: number): Array<number | null> {
  if (last <= 7) return Array.from({ length: last }, (_, i) => i + 1)
  const near = [page - 1, page, page + 1].filter((p) => p > 1 && p < last)
  if (page <= 4) return [1, 2, 3, 4, 5, null, last]
  if (page >= last - 3) return [1, null, last - 4, last - 3, last - 2, last - 1, last]
  return [1, null, ...near, null, last]
}

function Jump(props: { last: number; onPage: (p: number) => void }) {
  const [v, setV] = useState('')
  return (
    <label className="cb-pg-jump">
      跳至
      <input
        inputMode="numeric"
        aria-label={`跳至第几页，共 ${props.last} 页`}
        value={v}
        onChange={(e) => setV(e.target.value.replace(/\D/g, ''))}
        onKeyDown={(e) => {
          if (e.key !== 'Enter' || !v) return
          props.onPage(Math.min(props.last, Math.max(1, Number(v))))
          setV('')
        }}
      />
      页
    </label>
  )
}

/**
 * 列表分页条：只有超过一页时出现。
 * 标准版：左边「共 N 条 · 每页 10 条」，右边上一页 / 页码 / 下一页 / 跳至；compact 用于窄栏：上一页 · 1 / 3 · 下一页 · 跳至。
 */
export function Pager(props: { page: number; pageSize?: number; total: number; onPage: (p: number) => void; compact?: boolean; label?: string }) {
  const size = props.pageSize ?? PAGE_SIZE
  const last = Math.max(1, Math.ceil(props.total / size))
  if (props.total <= size) return null
  const go = (p: number) => props.onPage(Math.min(last, Math.max(1, p)))
  const prev = (
    <button type="button" className="cb-pg" aria-label="上一页" disabled={props.page <= 1} onClick={() => go(props.page - 1)}>
      <Icon name="chevronLeft" />
    </button>
  )
  const next = (
    <button type="button" className="cb-pg" aria-label="下一页" disabled={props.page >= last} onClick={() => go(props.page + 1)}>
      <Icon name="chevronRight" />
    </button>
  )
  if (props.compact) {
    return (
      <nav className="cb-pager cb-pager-compact" aria-label={props.label ?? '分页'}>
        {prev}
        <span className="cb-pg-of">
          {props.page} / {last}
        </span>
        {next}
        <Jump last={last} onPage={go} />
      </nav>
    )
  }
  return (
    <nav className="cb-pager" aria-label={props.label ?? '分页'}>
      <span className="cb-pager-total">
        共 {props.total.toLocaleString('zh-CN')} 条 · 每页 {size} 条
      </span>
      {prev}
      {pageItems(props.page, last).map((p, i) =>
        p == null ? (
          <span key={`gap-${i}`} className="cb-pg-gap" aria-hidden="true">
            …
          </span>
        ) : (
          <button key={p} type="button" className="cb-pg" aria-current={p === props.page ? 'page' : undefined} aria-label={`第 ${p} 页`} onClick={() => go(p)}>
            {p}
          </button>
        ),
      )}
      {next}
      <Jump last={last} onPage={go} />
    </nav>
  )
}

/** 前端分页：列表一次取回、按 10 条切片。数据变少时页码自动收回到最后一页。 */
export function usePaged<T>(items: readonly T[], size = PAGE_SIZE) {
  const [page, setPage] = useState(1)
  const last = Math.max(1, Math.ceil(items.length / size))
  const current = Math.min(page, last)
  return {
    page: current,
    setPage,
    rows: items.slice((current - 1) * size, current * size),
    pager: { page: current, pageSize: size, total: items.length, onPage: setPage },
  }
}
