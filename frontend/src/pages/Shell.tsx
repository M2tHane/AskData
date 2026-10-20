import { useState } from 'react'
import type { ReactNode } from 'react'
import { UnsavedDialog } from '../components/Dialog'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { QueryErrors } from '../components/QueryErrors'
import { Sidebar } from '../components/Sidebar'
import { ThemeToggle } from '../components/ThemeToggle'
import { navFor, ROLE_LABEL, type Route } from '../lib/nav'
import type { CurrentUser } from '../api/types'
import { useAuth } from '../state/auth'
import { useUnsaved } from '../state/unsaved'

/** 侧栏底部的账号行：头像 + 名字 + 角色 + 深浅色切换 + 退出。 */
function Account(props: { user: CurrentUser; onLogout: () => void }) {
  const username = useAuth((s) => s.username)
  const name = username ?? ROLE_LABEL[props.user.role]
  return (
    <>
      <span className="cb-avatar" aria-hidden="true" title={`${name} · ${ROLE_LABEL[props.user.role]}`}>
        {name.slice(0, 1).toUpperCase()}
      </span>
      <div className="cb-user">
        <b>{name}</b>
        <span>{ROLE_LABEL[props.user.role]}</span>
      </div>
      <ThemeToggle />
      <button type="button" className="cb-icon-btn" aria-label="退出登录" title="退出登录" onClick={props.onLogout}>
        <Icon name="logout" />
      </button>
    </>
  )
}

export function Shell(props: { user: CurrentUser; active: Route; onNavigate: (path: string) => void; onLogout: () => void; children: ReactNode }) {
  const { groups, bottom } = navFor(props.user.role)
  const { dirty, save, pending, ask } = useUnsaved()
  const [saving, setSaving] = useState(false)
  // 有未保存的修改时，先问再走。
  const go = (path: string) => {
    if (dirty && path !== window.location.pathname) ask(path)
    else props.onNavigate(path)
  }
  const leave = () => {
    const to = pending
    useUnsaved.getState().set(false, null)
    ask(null)
    if (to) props.onNavigate(to)
  }
  return (
    <div className="cb-app">
      <QueryErrors />
      <Sidebar
        groups={groups}
        bottom={bottom}
        active={props.active.id}
        onNavigate={(r) => go(r.path)}
        footer={<Account user={props.user} onLogout={() => (dirty ? ask('__logout__') : props.onLogout())} />}
      />
      <main className="cb-main">
        {props.active.id === 'chat' ? (
          props.children
        ) : (
          <PageHeader key={props.active.id} icon={props.active.icon} title={props.active.label}>
            {props.children}
          </PageHeader>
        )}
      </main>
      {pending ? (
        <UnsavedDialog
          saving={saving}
          onStay={() => ask(null)}
          onDiscard={() => (pending === '__logout__' ? (ask(null), props.onLogout()) : leave())}
          onSave={
            save
              ? async () => {
                  setSaving(true)
                  const ok = await save().catch(() => false)
                  setSaving(false)
                  if (!ok) return ask(null)
                  if (pending === '__logout__') {
                    ask(null)
                    props.onLogout()
                  } else leave()
                }
              : undefined
          }
        />
      ) : null}
    </div>
  )
}
