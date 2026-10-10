import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'

/**
 * 模型的思考过程。思考时展开并跟随滚动；一开始输出结论就自动折叠。
 * 用户手动展开或折叠后，以用户的选择为准。
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
      <button type="button" className="cb-reasoning-head" aria-expanded={open} onClick={() => setUserOpen(!open)}>
        <Icon name="sparkle" size="sm" />
        <span>{label}</span>
        {thinking ? <span className="cb-caret" aria-hidden="true" /> : null}
        <span className="cb-spacer" />
        <span className="cb-muted">{open ? '收起' : '展开'}</span>
        <Icon name="chevronDown" size="sm" className="cb-reasoning-chevron" />
      </button>
      {open ? (
        <div className="cb-reasoning-body" ref={body}>
          {text}
        </div>
      ) : null}
    </div>
  )
}
