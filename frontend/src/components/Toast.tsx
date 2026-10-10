import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'

export type ToastTone = 'success' | 'danger' | 'warning' | 'info'
export interface ToastInput {
  tone: ToastTone
  title: string
  message?: string
}
interface ToastItem extends ToastInput {
  id: number
}

const ICONS: Record<ToastTone, IconName> = { success: 'check', danger: 'alert', warning: 'alert', info: 'info' }
const DURATION: Record<ToastTone, number> = { success: 4000, info: 4000, warning: 6000, danger: 8000 }

const ToastContext = createContext<(t: ToastInput) => void>(() => undefined)

export const useToast = () => useContext(ToastContext)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const dismiss = useCallback((id: number) => setItems((cur) => cur.filter((t) => t.id !== id)), [])
  const push = useCallback((t: ToastInput) => setItems((cur) => [...cur.slice(-3), { ...t, id: Date.now() + Math.random() }]), [])
  const value = useMemo(() => push, [push])
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="cb-toasts" aria-live="polite">
        {items.map((t) => (
          <ToastCard key={t.id} item={t} onClose={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function ToastCard({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onClose, DURATION[item.tone])
    return () => clearTimeout(timer)
  }, [item.tone, onClose])
  return (
    <div className={`cb-toast cb-toast-${item.tone}`} role={item.tone === 'danger' ? 'alert' : 'status'}>
      <Icon name={ICONS[item.tone]} />
      <div className="cb-toast-body">
        <b>{item.title}</b>
        {item.message ? <div>{item.message}</div> : null}
      </div>
      <button type="button" className="cb-toast-close" aria-label="关闭提示" onClick={onClose}>
        <Icon name="x" size="sm" />
      </button>
    </div>
  )
}
