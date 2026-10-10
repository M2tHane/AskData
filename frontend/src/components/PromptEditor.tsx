import { useLayoutEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { usedVariables } from '../lib/diff'
import { Icon } from './Icon'

const TOKEN = /(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)|(<\/?untrusted_data>)|(^#{1,3} .*$|^[A-Z][A-Za-z /]{1,40}:$)/gm

/** 背景层：变量、数据块标签和小节标题着色；文字与上层输入框逐字重合。 */
function highlight(text: string, required: Set<string>): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(TOKEN)) {
    const at = m.index ?? 0
    if (at > last) out.push(text.slice(last, at))
    if (m[1]) {
      const name = m[1].replace(/^\$\{?|\}$/g, '')
      out.push(<mark key={at} className={required.has(name) ? 'cb-pe-var' : 'cb-pe-var cb-pe-bad'}>{m[1]}</mark>)
    } else if (m[2]) out.push(<mark key={at} className="cb-pe-tag">{m[2]}</mark>)
    else out.push(<mark key={at} className="cb-pe-head">{m[3]}</mark>)
    last = at + m[0].length
  }
  out.push(text.slice(last))
  // 末尾换行需要一个占位字符，否则背景层比输入框少一行。
  out.push('\u200b')
  return out
}

/**
 * 提示词编辑器：高度随内容增长（超过视口后内部滚动），变量高亮，底部状态行逐个核对必须保留的变量。
 */
export function PromptEditor(props: { value: string; onChange: (value: string) => void; variables: string[]; label: string }) {
  const area = useRef<HTMLTextAreaElement>(null)
  const back = useRef<HTMLPreElement>(null)
  const required = new Set(props.variables)
  const used = new Set(usedVariables(props.value))
  const lines = props.value.split('\n').length

  useLayoutEffect(() => {
    const el = area.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight + 2}px`
  }, [props.value])

  return (
    <div className="cb-pe">
      <div className="cb-pe-box">
        <pre ref={back} className="cb-pe-back" aria-hidden="true">
          {highlight(props.value, required)}
        </pre>
        <textarea
          ref={area}
          className="cb-pe-input"
          aria-label={props.label}
          value={props.value}
          spellCheck={false}
          onChange={(e) => props.onChange(e.target.value)}
          onScroll={(e) => {
            if (back.current) back.current.scrollTop = e.currentTarget.scrollTop
          }}
        />
      </div>
      <div className="cb-pe-status">
        <span>
          {lines} 行 · {props.value.length.toLocaleString()} 字
        </span>
        <span className="cb-spacer" />
        {props.variables.map((v) => (
          <span key={v} className={used.has(v) ? 'cb-pe-ok' : 'cb-pe-missing'}>
            <Icon name={used.has(v) ? 'check' : 'alert'} size="sm" />${v}
          </span>
        ))}
      </div>
    </div>
  )
}
