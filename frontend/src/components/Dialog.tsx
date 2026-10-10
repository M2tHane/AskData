import { useEffect } from 'react'
import type { ReactNode } from 'react'

export function Dialog(props: { title: string; onClose: () => void; size?: 'md' | 'lg' | 'xl' | 'wide'; children: ReactNode }) {
  const { onClose } = props
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <>
      <div className="cb-dialog-backdrop" onClick={onClose} />
      <div className={`cb-dialog cb-dialog-${props.size ?? 'md'}`} role="dialog" aria-modal="true" aria-label={props.title}>
        <h3 className="cb-h">{props.title}</h3>
        {props.children}
      </div>
    </>
  )
}

/** 底部操作区：左侧放辅助操作（如测试连接），右侧放取消与保存。 */
export function DialogFooter(props: { left?: ReactNode; children: ReactNode }) {
  return (
    <div className="cb-dialog-foot">
      <div className="cb-row">{props.left}</div>
      <div className="cb-row">{props.children}</div>
    </div>
  )
}
