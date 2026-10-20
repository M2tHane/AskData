import { useState } from 'react'
import type { ReactNode } from 'react'
import { Icon } from './Icon'
import type { NavGroup, Route } from '../lib/nav'

const KEY = 'askdata.sidebar.collapsed'

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

function NavItem(props: { route: Route; active: boolean; onNavigate: (r: Route) => void }) {
  const { route } = props
  return (
    <a
      href={route.path}
      className="cb-nav"
      aria-current={props.active ? 'page' : undefined}
      title={route.label}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
        e.preventDefault()
        props.onNavigate(route)
      }}
    >
      <Icon name={route.icon} />
      <span>{route.label}</span>
    </a>
  )
}

/**
 * 侧栏：品牌标记 → 业务入口（分组）→ 治理与系统入口（平铺沉底）→ 账号行。
 * 导航项后面不带数字；窄于 1024px 或手动收起时变成 60px 图标栏。
 */
export function Sidebar(props: { groups: NavGroup[]; bottom: Route[]; active: string; onNavigate: (r: Route) => void; footer: ReactNode }) {
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggle = () => {
    const next = !collapsed
    setCollapsed(next)
    try {
      localStorage.setItem(KEY, next ? '1' : '0')
    } catch {
      /* not persisted */
    }
  }
  return (
    <aside className="cb-side" data-collapsed={collapsed || undefined}>
      <div className="cb-side-head">
        <div className="cb-brand">
          <span className="cb-mark" aria-hidden="true">
            A
          </span>
          <span className="cb-brand-name">AskData</span>
        </div>
        <button type="button" className="cb-icon-btn cb-side-toggle" aria-label={collapsed ? '展开侧栏' : '收起侧栏'} aria-expanded={!collapsed} onClick={toggle}>
          <Icon name={collapsed ? 'sidebarExpand' : 'sidebarCollapse'} />
        </button>
      </div>
      <nav className="cb-side-top" aria-label="主导航">
        {props.groups.map((g) => (
          <div className="cb-nav-group" key={g.label}>
            <div className="cb-nav-label">{g.label}</div>
            {g.items.map((r) => (
              <NavItem key={r.id} route={r} active={props.active === r.id} onNavigate={props.onNavigate} />
            ))}
          </div>
        ))}
      </nav>
      <div className="cb-side-bottom">
        {props.bottom.length ? (
          <nav className="cb-nav-group" aria-label="治理与系统">
            {props.bottom.map((r) => (
              <NavItem key={r.id} route={r} active={props.active === r.id} onNavigate={props.onNavigate} />
            ))}
          </nav>
        ) : null}
        <div className="cb-side-foot">{props.footer}</div>
      </div>
    </aside>
  )
}
