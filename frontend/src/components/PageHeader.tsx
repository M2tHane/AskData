import { createContext, useContext, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'

const SlotContext = createContext<HTMLElement | null>(null)

/**
 * 页头：22px 图标 + 页标题，右侧是本页的操作（刷新、导出、新建…）。对话页不用。
 * 页头由外壳按当前路由渲染，页面用 <HeaderActions> 把按钮放进右侧操作区。
 */
export function PageHeader(props: { icon: IconName; title: string; children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null)
  return (
    <SlotContext.Provider value={slot}>
      <header className="cb-header">
        <Icon name={props.icon} className="cb-icon-lg" />
        <h1>{props.title}</h1>
        <div className="cb-header-actions" ref={setSlot} />
      </header>
      {props.children}
    </SlotContext.Provider>
  )
}

/** 把本页的操作按钮放进页头右侧。 */
export function HeaderActions(props: { children: ReactNode }) {
  const slot = useContext(SlotContext)
  return slot ? createPortal(props.children, slot) : null
}

/** 数据新鲜度：绿点 +「刚刚同步」之类，放在页头操作区最左。 */
export function SyncNote(props: { children: ReactNode }) {
  return (
    <span className="cb-sync">
      <i aria-hidden="true" />
      {props.children}
    </span>
  )
}
