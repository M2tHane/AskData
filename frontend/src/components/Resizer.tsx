import { useRef } from 'react'

/** 面板之间的拖拽条：拖动、方向键调整宽度，双击恢复默认。 */
export function Resizer(props: {
  label: string
  /** left：拖条在被调整面板的右侧；right：拖条在被调整面板的左侧（向左拖变宽）。 */
  side: 'left' | 'right'
  value: number
  min: number
  max: number
  defaultValue: number
  onChange: (px: number) => void
}) {
  const start = useRef<{ x: number; width: number } | null>(null)
  const clamp = (n: number) => Math.min(props.max, Math.max(props.min, Math.round(n)))
  const apply = (dx: number) => props.onChange(clamp((start.current?.width ?? props.value) + (props.side === 'left' ? dx : -dx)))
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={props.label}
      aria-valuenow={props.value}
      aria-valuemin={props.min}
      aria-valuemax={props.max}
      tabIndex={0}
      className="cb-resizer"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture?.(e.pointerId)
        start.current = { x: e.clientX, width: props.value }
      }}
      onPointerMove={(e) => {
        if (start.current) apply(e.clientX - start.current.x)
      }}
      onPointerUp={() => {
        start.current = null
      }}
      onDoubleClick={() => props.onChange(props.defaultValue)}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 40 : 10
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault()
          const dx = e.key === 'ArrowRight' ? step : -step
          props.onChange(clamp(props.value + (props.side === 'left' ? dx : -dx)))
        }
      }}
    >
      <span />
    </div>
  )
}

const KEY = 'askdata.layout'

export interface Layout {
  left: number
  right: number
  collapsed: boolean
  leftCollapsed: boolean
}

export const DEFAULT_LAYOUT: Layout = { left: 220, right: 380, collapsed: false, leftCollapsed: false }

export function loadLayout(): Layout {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Partial<Layout> | null
    if (v) return { left: Number(v.left) || DEFAULT_LAYOUT.left, right: Number(v.right) || DEFAULT_LAYOUT.right, collapsed: !!v.collapsed, leftCollapsed: !!v.leftCollapsed }
  } catch {
    /* ignore corrupted layout */
  }
  return DEFAULT_LAYOUT
}

export function saveLayout(layout: Layout): void {
  localStorage.setItem(KEY, JSON.stringify(layout))
}
