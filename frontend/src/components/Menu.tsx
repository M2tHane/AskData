import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon, type IconName } from './Icon'

export interface MenuItem {
  label: string
  icon?: IconName
  danger?: boolean
  onSelect: () => void
}

/** 行尾的「⋯」菜单：点开是一列操作，Esc、点外面或滚动都会关上；上下键移动。 */
export function MoreMenu(props: { label: string; items: MenuItem[]; className?: string }) {
  const [rect, setRect] = useState<{ right: number; top: number; bottom: number } | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const open = rect != null
  const close = (refocus = true) => {
    setRect(null)
    if (refocus) trigger.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    menu.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (!menu.current?.contains(t) && !trigger.current?.contains(t)) setRect(null)
    }
    const onScroll = (e: Event) => {
      if (!menu.current?.contains(e.target as Node)) setRect(null)
    }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', () => setRect(null), { once: true })
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])

  const onKeyDown = (e: React.KeyboardEvent) => {
    const items = [...(menu.current?.querySelectorAll<HTMLButtonElement>('button') ?? [])]
    const i = items.indexOf(document.activeElement as HTMLButtonElement)
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      close()
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const next = items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]
      next?.focus()
    } else if (e.key === 'Tab') {
      close(false)
    }
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={['cb-icon-btn cb-more', props.className].filter(Boolean).join(' ')}
        aria-label={props.label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation()
          if (open) return close()
          const r = e.currentTarget.getBoundingClientRect()
          setRect({ right: window.innerWidth - r.right, top: r.top, bottom: r.bottom })
        }}
      >
        <Icon name="more" />
      </button>
      {rect
        ? createPortal(
            <div
              ref={menu}
              role="menu"
              aria-label={props.label}
              className="cb-menu"
              style={{ right: rect.right, ...(window.innerHeight - rect.bottom < 120 ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.bottom + 4 }) }}
              onKeyDown={onKeyDown}
              onClick={(e) => e.stopPropagation()}
            >
              {props.items.map((it) => (
                <button
                  key={it.label}
                  type="button"
                  role="menuitem"
                  className={it.danger ? 'cb-menu-item cb-menu-danger' : 'cb-menu-item'}
                  onClick={() => {
                    close(false)
                    it.onSelect()
                  }}
                >
                  {it.icon ? <Icon name={it.icon} /> : null}
                  {it.label}
                </button>
              ))}
            </div>,
            document.body,
          )
        : null}
    </>
  )
}
