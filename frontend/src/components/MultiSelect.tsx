import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { Icon } from './Icon'

export interface MultiOption {
  value: number
  label: string
  hint?: string
}

interface Props {
  options: MultiOption[]
  value: number[]
  onChange: (value: number[]) => void
  /** 触发按钮上的内容；不传则显示「已选 N 项」。 */
  children?: ReactNode
  ariaLabel: string
  disabled?: boolean
  size?: 'md' | 'sm'
  emptyText?: string
  /** 弹层底部的附加操作，例如「恢复为继承」。 */
  footer?: ReactNode
  /** 触发按钮样式：select 与下拉框一致，link 是行内文字按钮。 */
  variant?: 'select' | 'link'
}

/** 可多选的自绘下拉：勾选即生效，选项多时带搜索；Esc 只关闭弹层，不会连带关闭外层对话框。 */
export function MultiSelect(props: Props) {
  const { options, value, onChange, disabled, size = 'md', variant = 'select' } = props
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [rect, setRect] = useState<{ left: number; top: number; width: number; above: boolean } | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const listId = useId()
  const chosen = new Set(value)

  const show = () => {
    if (disabled) return
    const r = trigger.current?.getBoundingClientRect()
    if (r) {
      const room = window.innerHeight - r.bottom
      const above = room < 300 && r.top > room
      setRect({ left: r.left, top: above ? r.top : r.bottom, width: r.width, above })
    }
    setQ('')
    setOpen(true)
  }
  const close = () => setOpen(false)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (!root.current?.contains(t) && !list.current?.contains(t)) close()
    }
    const onScroll = (e: Event) => {
      if (!list.current?.contains(e.target as Node)) close()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        close()
        trigger.current?.focus()
      }
    }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', onScroll, true)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  const toggle = (id: number) => {
    const next = new Set(chosen)
    if (!next.delete(id)) next.add(id)
    onChange([...next])
  }
  const shown = options.filter((o) => o.label.toLowerCase().includes(q.trim().toLowerCase()))

  return (
    <div ref={root} className="cb-sel cb-multi">
      <button
        ref={trigger}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={props.ariaLabel}
        disabled={disabled}
        className={variant === 'link' ? 'cb-link' : `cb-sel-trigger${size === 'sm' ? ' cb-sel-sm' : ''}`}
        onClick={() => (open ? close() : show())}
      >
        <span>{props.children ?? `已选 ${value.length} 项`}</span>
        {variant === 'select' ? <Icon name="chevronDown" size="sm" /> : null}
      </button>
      {open && rect
        ? createPortal(
        <div
          ref={list}
          id={listId}
          className="cb-sel-pop cb-multi-pop"
          style={{ left: rect.left, minWidth: Math.max(rect.width, 200), ...(rect.above ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.top + 4 }) }}
        >
          {options.length > 6 ? (
            <div className="cb-menu-search">
              <Icon name="search" />
              <input type="search" aria-label="搜索" placeholder="搜索" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
            </div>
          ) : null}
          <ul role="listbox" aria-multiselectable="true" aria-label={props.ariaLabel}>
            {shown.length === 0 ? <li className="cb-sel-empty">{props.emptyText ?? '没有可选项'}</li> : null}
            {shown.map((o) => (
              <li key={o.value} role="option" aria-selected={chosen.has(o.value)}>
                <label>
                  <input type="checkbox" checked={chosen.has(o.value)} onChange={() => toggle(o.value)} />
                  <span>{o.label}</span>
                  {o.hint ? <small className="cb-faint">{o.hint}</small> : null}
                </label>
              </li>
            ))}
          </ul>
          {props.footer ? <div className="cb-multi-foot">{props.footer}</div> : null}
        </div>,
          document.body,
        )
        : null}
    </div>
  )
}
