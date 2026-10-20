import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'

export type ToastTone = 'success' | 'danger' | 'warning' | 'info'
export interface ToastInput {
  tone: ToastTone
  title: string
  /** 一行说明。 */
  message?: string
  /** 可选的一个文字动作，如「查看」「撤销」。 */
  action?: { label: string; onClick: () => void }
}
interface ToastItem extends ToastInput {
  id: number
}

const ICONS: Record<ToastTone, IconName> = { success: 'check', danger: 'alert', warning: 'alert', info: 'info' }
/** 默认 4 秒；带动作的给 6 秒，留出点「撤销」的时间。 */
const duration = (t: ToastInput) => (t.action ? 6000 : 4000)
const MAX = 3

const ToastContext = createContext<(t: ToastInput) => void>(() => undefined)

export const useToast = () => useContext(ToastContext)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const seq = useRef(0)
  const dismiss = useCallback((id: number) => setItems((cur) => cur.filter((t) => t.id !== id)), [])
  // 新的在下面；超过 3 张时最早的一张先消失。
  const push = useCallback((t: ToastInput) => setItems((cur) => [...cur.slice(-(MAX - 1)), { ...t, id: ++seq.current }]), [])
  const value = useMemo(() => push, [push])
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="cb-toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <ToastCard key={t.id} item={t} onClose={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function ToastCard({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  const [paused, setPaused] = useState(false)
  // 每次移开鼠标重新计时，进度条也从头走。
  const [round, setRound] = useState(0)
  const ms = duration(item)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })
  useEffect(() => {
    if (paused) return
    const timer = setTimeout(() => close.current(), ms)
    return () => clearTimeout(timer)
  }, [paused, round, ms])
  return (
    <div
      className={`cb-toast cb-toast-${item.tone}`}
      data-paused={paused || undefined}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => {
        setPaused(false)
        setRound((r) => r + 1)
      }}
    >
      <span className="cb-toast-ic" aria-hidden="true">
        <Icon name={ICONS[item.tone]} />
      </span>
      <div className="cb-toast-text">
        <b>{item.title}</b>
        {item.message ? <span>{item.message}</span> : null}
        {item.action ? (
          <button
            type="button"
            className="cb-link"
            onClick={() => {
              item.action?.onClick()
              onClose()
            }}
          >
            {item.action.label}
          </button>
        ) : null}
      </div>
      <button type="button" className="cb-icon-btn" aria-label="关闭提示" onClick={onClose}>
        <Icon name="x" size="sm" />
      </button>
      <span key={round} className="cb-toast-bar" style={{ animationDuration: `${ms}ms` }} aria-hidden="true" />
    </div>
  )
}
