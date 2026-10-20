import { useLayoutEffect, useMemo, useRef } from 'react'
import { usedVariables } from '../lib/diff'
import { FORMAT_LABEL, tokenize, type FormatIssue, type PromptFormat } from '../lib/promptFormat'
import { Button } from './basic'
import { Icon } from './Icon'

/**
 * 提示词编辑器：背景层高亮 + 上层透明输入框，两层字体、内边距、换行完全一致。
 * 高度随内容增长（超过视口后内部滚动）；顶部显示识别出的格式和「格式化」，底部状态栏是格式校验和必需变量检查。
 */
export function PromptEditor(props: {
  value: string
  onChange: (value: string) => void
  variables: string[]
  label: string
  format: PromptFormat
  /** YAML 解析错误；有值时状态栏显示行号。 */
  issue: FormatIssue | null
  /** 变量、数据块等内容问题；有值时状态栏显示第一条。 */
  problems: string[]
  onFormat: () => void
}) {
  const area = useRef<HTMLTextAreaElement>(null)
  const back = useRef<HTMLPreElement>(null)
  const required = useMemo(() => new Set(props.variables), [props.variables])
  const used = new Set(usedVariables(props.value))
  const lines = props.value.split('\n').length
  const tokens = useMemo(() => tokenize(props.value, props.format), [props.value, props.format])

  useLayoutEffect(() => {
    const el = area.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight + 2}px`
  }, [props.value])

  const first = props.issue ? `第 ${props.issue.line} 行：${props.issue.message}` : props.problems[0]

  return (
    <div className="cb-pr-editor">
      <div className="cb-pr-bar">
        <span className="cb-pr-format">{FORMAT_LABEL[props.format]}</span>
        <span className="cb-faint">按内容自动识别</span>
        <span className="cb-spacer" />
        <Button size="sm" variant="ghost" onClick={props.onFormat}>格式化</Button>
      </div>
      <div className="cb-pr-box">
        <pre ref={back} className="cb-pr-back" aria-hidden="true">
          {tokens.map((t, i) =>
            t.kind === 'plain' ? (
              t.text
            ) : (
              <mark key={i} className={`cb-tk cb-tk-${t.kind}${t.kind === 'var' && !required.has(t.name ?? '') ? ' cb-tk-bad' : ''}`}>
                {t.text}
              </mark>
            ),
          )}
          {/* 末尾换行需要一个占位字符，否则背景层比输入框少一行。 */}
          {'\u200b'}
        </pre>
        <textarea
          ref={area}
          className="cb-pr-input"
          aria-label={props.label}
          value={props.value}
          spellCheck={false}
          onChange={(e) => props.onChange(e.target.value)}
          onScroll={(e) => {
            if (back.current) back.current.scrollTop = e.currentTarget.scrollTop
          }}
        />
      </div>
      <div className="cb-pr-status">
        {first ? (
          <span className="cb-pr-problem" role="alert">
            <Icon name="alert" size="sm" />
            {first}
          </span>
        ) : (
          <span className="cb-pr-fine">
            <Icon name="check" size="sm" />
            格式正确
          </span>
        )}
        <span className="cb-faint">
          {lines} 行 · {props.value.length.toLocaleString()} 字
        </span>
        <span className="cb-spacer" />
        {props.variables.map((v) => (
          <span key={v} className={used.has(v) ? 'cb-pr-ok' : 'cb-pr-missing'}>
            <Icon name={used.has(v) ? 'check' : 'alert'} size="sm" />${v}
          </span>
        ))}
      </div>
    </div>
  )
}
