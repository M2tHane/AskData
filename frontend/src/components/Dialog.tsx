import { useEffect, useId, useRef } from 'react'
import type { ReactNode, RefObject } from 'react'
import { Button } from './basic'
import { Icon } from './Icon'

/** 打开中的浮层，后开的在栈顶；Esc 只关栈顶那一个。 */
const stack: symbol[] = []

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** 浮层的公共行为：Esc 关闭栈顶、打开时把焦点移进去、Tab 不跑出浮层、关闭后焦点回到原处。 */
function useLayer(box: RefObject<HTMLElement | null>, onClose: () => void) {
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })
  useEffect(() => {
    const id = Symbol('layer')
    stack.push(id)
    const before = document.activeElement as HTMLElement | null
    const el = box.current
    const first = el?.querySelector<HTMLElement>('[autofocus], [data-autofocus]') ?? el?.querySelector<HTMLElement>(`.cb-dialog-body ${FOCUSABLE}, .cb-drawer-body ${FOCUSABLE}`) ?? el
    first?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (stack[stack.length - 1] !== id) return
      if (e.key === 'Escape') {
        e.preventDefault()
        close.current()
      } else if (e.key === 'Tab' && el) {
        const items = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null)
        if (items.length === 0) return
        const head = items[0]
        const tail = items[items.length - 1]
        if (e.shiftKey && document.activeElement === head) {
          e.preventDefault()
          tail?.focus()
        } else if (!e.shiftKey && document.activeElement === tail) {
          e.preventDefault()
          head?.focus()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      stack.splice(stack.indexOf(id), 1)
      if (before?.isConnected) before.focus()
    }
  }, [box])
}

export function Dialog(props: { title: string; onClose: () => void; size?: 'md' | 'lg' | 'xl' | 'wide'; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  const titleId = useId()
  useLayer(box, props.onClose)
  return (
    <>
      <div className="cb-dialog-backdrop" onClick={props.onClose} />
      <div ref={box} tabIndex={-1} className={`cb-dialog cb-dialog-${props.size ?? 'md'}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="cb-dialog-head">
          <h2 id={titleId}>{props.title}</h2>
          <button type="button" className="cb-icon-btn" aria-label="关闭" onClick={props.onClose}>
            <Icon name="x" />
          </button>
        </div>
        <div className="cb-dialog-body">{props.children}</div>
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

/** 右侧抽屉：列表行的详情。wide 用于树形内容。 */
export function Drawer(props: { title: ReactNode; label?: string; onClose: () => void; wide?: boolean; actions?: ReactNode; footer?: ReactNode; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  const titleId = useId()
  useLayer(box, props.onClose)
  return (
    <>
      <div className="cb-dialog-backdrop" onClick={props.onClose} />
      <aside ref={box} tabIndex={-1} className={`cb-drawer${props.wide ? ' cb-drawer-wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={props.label ? undefined : titleId} aria-label={props.label}>
        <div className="cb-drawer-head">
          <h2 id={titleId}>{props.title}</h2>
          {props.actions}
          <button type="button" className="cb-icon-btn" aria-label="关闭" onClick={props.onClose}>
            <Icon name="x" />
          </button>
        </div>
        <div className="cb-drawer-body">{props.children}</div>
        {props.footer ? <div className="cb-drawer-foot">{props.footer}</div> : null}
      </aside>
    </>
  )
}

/** 删除等不可撤销操作的二次确认。 */
export function ConfirmDialog(props: {
  title: string
  children?: ReactNode
  confirmLabel: string
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog title={props.title} onClose={props.onClose}>
      {props.children ? <div>{props.children}</div> : null}
      <DialogFooter>
        <Button onClick={props.onClose}>取消</Button>
        <Button variant={props.danger ? 'danger' : 'primary'} disabled={props.busy} onClick={props.onConfirm}>
          {props.confirmLabel}
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

/** 有未保存的修改时切换对象或离开页面：放弃并离开 / 继续编辑 / 保存并离开。 */
export function UnsavedDialog(props: { onDiscard: () => void; onStay: () => void; onSave?: () => void; saving?: boolean; canSave?: boolean }) {
  return (
    <Dialog title="有未保存的修改" onClose={props.onStay}>
      <p>离开后，这些修改会丢失。</p>
      <DialogFooter left={<Button variant="ghost" onClick={props.onDiscard}>放弃并离开</Button>}>
        <Button onClick={props.onStay} data-autofocus>
          继续编辑
        </Button>
        {props.onSave ? (
          <Button variant="primary" disabled={props.saving || props.canSave === false} onClick={props.onSave}>
            {props.saving ? '保存中…' : '保存并离开'}
          </Button>
        ) : null}
      </DialogFooter>
    </Dialog>
  )
}
