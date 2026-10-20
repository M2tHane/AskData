import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'

/**
 * 模型的思考过程：结论上方一行可折叠的灰字「已思考 N 秒」。
 * 思考时自动展开并跟随滚动；一开始输出结论就自动折叠。用户手动展开或折叠后，以用户为准。
 */
export function ReasoningPanel(props: { text: string; thinking: boolean; answering: boolean; seconds?: number }) {
  const { text, thinking, answering, seconds } = props
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  const open = userOpen ?? !answering
  const body = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = body.current
    if (el && open && thinking) el.scrollTop = el.scrollHeight
  }, [text, open, thinking])
  if (!text) return null
  const label = thinking ? '正在思考' : seconds != null && seconds > 0 ? `已思考 ${seconds} 秒` : '思考过程'
  return (
    <div className="cb-reasoning" data-open={open}>
      <button type="button" className="cb-think" aria-expanded={open} onClick={() => setUserOpen(!open)}>
        <Icon name="chevronDown" className="cb-chev" />
        <Icon name="sparkle" />
        <span>{label}</span>
        {thinking ? <span className="cb-caret" aria-hidden="true" /> : null}
      </button>
      {open ? (
        <div className="cb-think-body" ref={body}>
          {text}
        </div>
      ) : null}
    </div>
  )
}
