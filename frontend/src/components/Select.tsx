import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './Icon'

export interface SelectOption<T extends string | number> {
  value: T
  label: string
  hint?: string
  disabled?: boolean
}

interface Props<T extends string | number> {
  value: T | null | undefined
  onChange: (value: T) => void
  options: SelectOption<T>[]
  ariaLabel?: string
  placeholder?: string
  disabled?: boolean
  size?: 'md' | 'sm'
  className?: string
  id?: string
}

/** 平台统一的下拉框：不使用浏览器原生样式，支持键盘（上下键、回车、Esc、Home/End）。 */
export function Select<T extends string | number>(props: Props<T>) {
  const { value, onChange, options, placeholder = '请选择', disabled, size = 'md' } = props
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [rect, setRect] = useState<{ left: number; top: number; width: number; above: boolean } | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLUListElement>(null)
  const listId = useId()
  const selectedIndex = options.findIndex((o) => o.value === value)
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined

  const show = () => {
    if (disabled) return
    const r = trigger.current?.getBoundingClientRect()
    if (r) {
      const room = window.innerHeight - r.bottom
      setRect({ left: r.left, top: room < 260 && r.top > room ? r.top : r.bottom, width: r.width, above: room < 260 && r.top > room })
    }
    setActive(Math.max(selectedIndex, 0))
    setOpen(true)
  }
  const close = () => setOpen(false)
  const pick = (o: SelectOption<T>) => {
    if (o.disabled) return
    onChange(o.value)
    close()
    trigger.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (!root.current?.contains(t) && !menu.current?.contains(t)) close()
    }
    const onMove = () => close()
    const onScroll = (e: Event) => {
      if (!menu.current?.contains(e.target as Node)) close()
    }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('resize', onMove)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('resize', onMove)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
        e.preventDefault()
        show()
      }
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      close()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => Math.min(i + 1, options.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Home') {
      e.preventDefault()
      setActive(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      setActive(options.length - 1)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const o = options[active]
      if (o) pick(o)
    } else if (e.key === 'Tab') {
      close()
    }
  }

  return (
    <div ref={root} className={['cb-sel', props.className].filter(Boolean).join(' ')}>
      <button
        ref={trigger}
        id={props.id}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={props.ariaLabel}
        disabled={disabled}
        className={`cb-sel-trigger${size === 'sm' ? ' cb-sel-sm' : ''}`}
        onClick={() => (open ? close() : show())}
        onKeyDown={onKeyDown}
      >
        <span className={selected ? undefined : 'cb-muted'}>{selected ? selected.label : placeholder}</span>
        <Icon name="chevronDown" size="sm" />
      </button>
      {open && rect
        ? createPortal(
        <ul
          ref={menu}
          id={listId}
          role="listbox"
          className="cb-sel-list"
          style={{ left: rect.left, minWidth: rect.width, ...(rect.above ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.top + 4 }) }}
        >
          {options.length === 0 ? <li className="cb-sel-empty">没有可选项</li> : null}
          {options.map((o, i) => (
            <li
              key={String(o.value)}
              role="option"
              aria-selected={o.value === value}
              aria-disabled={o.disabled || undefined}
              data-active={i === active || undefined}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault()
                pick(o)
              }}
            >
              <span>{o.label}</span>
              {o.hint ? <small className="cb-muted">{o.hint}</small> : null}
              {o.value === value ? <Icon name="check" size="sm" /> : null}
            </li>
          ))}
        </ul>,
          document.body,
        )
        : null}
    </div>
  )
}
