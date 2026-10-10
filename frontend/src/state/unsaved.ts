import { useEffect } from 'react'
import { create } from 'zustand'

/**
 * 全局的「有未保存修改」标记：语义、提示词、权限页在有修改时登记，
 * 侧栏导航和浏览器关闭/刷新都先检查它。
 */
interface UnsavedState {
  dirty: boolean
  /** 保存当前修改；成功返回 true。 */
  save: (() => Promise<boolean>) | null
  /** 等待用户决定的跳转目标。 */
  pending: string | null
  set: (dirty: boolean, save: (() => Promise<boolean>) | null) => void
  ask: (path: string | null) => void
}

export const useUnsaved = create<UnsavedState>((set) => ({
  dirty: false,
  save: null,
  pending: null,
  set: (dirty, save) => set({ dirty, save }),
  ask: (pending) => set({ pending }),
}))

/** 页面里调用：有修改时登记，离开页面时撤销；同时拦截浏览器关闭和刷新。 */
export function useUnsavedGuard(dirty: boolean, save: (() => Promise<boolean>) | null) {
  useEffect(() => {
    useUnsaved.getState().set(dirty, dirty ? save : null)
  }, [dirty, save])
  useEffect(() => () => useUnsaved.getState().set(false, null), [])
  useEffect(() => {
    if (!dirty) return
    const onBefore = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onBefore)
    return () => window.removeEventListener('beforeunload', onBefore)
  }, [dirty])
}
