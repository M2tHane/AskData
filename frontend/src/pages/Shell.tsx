import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Sidebar } from '../components/basic'
import { Icon } from '../components/Icon'
import { QueryErrors } from '../components/QueryErrors'
import { ThemeToggle } from '../components/ThemeToggle'
import { navFor, ROLE_LABEL, type Route } from '../lib/nav'
import type { CurrentUser } from '../api/types'
import { useAuth } from '../state/auth'

/** 左下角的用户入口：显示用户名与角色，点开后可以退出登录。 */
function UserMenu(props: { user: CurrentUser; onLogout: () => void }) {
  const username = useAuth((s) => s.username)
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const name = username ?? ROLE_LABEL[props.user.role]

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={root} className="cb-user">
      {open ? (
        <div className="cb-user-menu" role="menu">
          <button type="button" role="menuitem" onClick={props.onLogout}>
            <Icon name="logout" size="sm" />
            退出登录
          </button>
        </div>
      ) : null}
      <button type="button" className="cb-user-btn" aria-haspopup="menu" aria-expanded={open} aria-label={`账号 ${name}`} onClick={() => setOpen((v) => !v)}>
        <span className="cb-avatar" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span>
        <span className="cb-user-text">
          <span className="cb-user-name">{name}</span>
          <span className="cb-muted">{ROLE_LABEL[props.user.role]}</span>
        </span>
        <Icon name="chevronDown" size="sm" />
      </button>
      <ThemeToggle />
    </div>
  )
}

export function Shell(props: { user: CurrentUser; active: Route; onNavigate: (path: string) => void; onLogout: () => void; children: ReactNode }) {
  const { groups, routes } = navFor(props.user.role)
  return (
    <div className="cb-app">
      <QueryErrors />
      <Sidebar
        groups={groups}
        active={props.active.id}
        onNavigate={(id) => {
          const r = routes.find((x) => x.id === id)
          if (r) props.onNavigate(r.path)
        }}
        footer={<UserMenu user={props.user} onLogout={props.onLogout} />}
      />
      <div className="cb-main">
        {props.children}
      </div>
    </div>
  )
}
