import { useId, useRef, useState } from 'react'
import type { ButtonHTMLAttributes, InputHTMLAttributes, KeyboardEvent, ReactNode, CSSProperties } from 'react'
import { Icon, type IconName } from './Icon'

const cx = (...parts: Array<string | false | null | undefined>) => parts.filter(Boolean).join(' ')

export type Scope = 'platform' | 'team' | 'personal'
export type Tone = 'neutral' | 'success' | 'danger' | 'warning' | 'info' | 'accent'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  /** sm：表格行内；lg：只在登录页用。 */
  size?: 'md' | 'sm' | 'lg'
  block?: boolean
  icon?: IconName
}

export function Button({ variant = 'secondary', size, block, icon, className, children, ...rest }: ButtonProps) {
  const iconOnly = !children && icon
  return (
    <button
      type="button"
      {...rest}
      className={cx('cb-btn', `cb-btn-${variant}`, size === 'sm' && 'cb-btn-sm', size === 'lg' && 'cb-btn-lg', block && 'cb-btn-block', iconOnly && 'cb-btn-icon', className)}
    >
      {icon ? <Icon name={icon} size={size === 'sm' ? 'sm' : undefined} /> : null}
      {children}
    </button>
  )
}

export function Badge(props: { tone?: Tone; icon?: IconName; mono?: boolean; outline?: boolean; children?: ReactNode }) {
  const { tone = 'neutral', icon, mono, outline, children } = props
  return (
    <span className={cx('cb-badge', `cb-badge-${tone}`, mono && 'cb-badge-mono', outline && 'cb-badge-outline', icon && 'cb-badge-icon')}>
      {icon ? <Icon name={icon} size="sm" /> : null}
      {children}
    </span>
  )
}

const SCOPE_LABEL: Record<Scope, string> = { platform: '平台级', team: '团队级', personal: '个人级' }

export function ScopeTag({ level = 'platform', title, children }: { level?: Scope; title?: string; children?: ReactNode }) {
  return (
    <span className={`cb-scope cb-scope-${level}`} title={title}>
      {children ?? SCOPE_LABEL[level]}
    </span>
  )
}

export interface TabItem {
  id: string
  label: string
  icon?: IconName
  count?: number
}

export function Tabs(props: { items: TabItem[]; value: string; onChange: (id: string) => void; label?: string }) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = props.items.findIndex((it) => it.id === props.value)
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    const to = e.key === 'Home' ? 0 : e.key === 'End' ? props.items.length - 1 : step ? (i + step + props.items.length) % props.items.length : -1
    const next = props.items[to]
    if (!next) return
    e.preventDefault()
    props.onChange(next.id)
    refs.current[to]?.focus()
  }
  return (
    <div className="cb-tabs" role="tablist" aria-label={props.label} onKeyDown={onKeyDown}>
      {props.items.map((it, i) => (
        <button
          key={it.id}
          ref={(el) => {
            refs.current[i] = el
          }}
          type="button"
          role="tab"
          className="cb-tab"
          aria-selected={props.value === it.id}
          tabIndex={props.value === it.id ? 0 : -1}
          onClick={() => props.onChange(it.id)}
        >
          {it.icon ? <Icon name={it.icon} size="sm" /> : null}
          {it.label}
          {it.count != null ? <span className="cb-num">{it.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

/** 分段控件：同一份数据的几个视角（2–6 个，每个不超过 6 个字）。 */
export function Segmented<T extends string>(props: {
  items: { id: T; label: ReactNode; icon?: IconName; title?: string }[]
  value: T
  onChange: (id: T) => void
  label: string
  className?: string
}) {
  return (
    <div className={cx('cb-seg', props.className)} role="group" aria-label={props.label}>
      {props.items.map((it) => (
        <button key={it.id} type="button" aria-pressed={props.value === it.id} title={it.title} onClick={() => props.onChange(it.id)}>
          {it.icon ? <Icon name={it.icon} size="sm" /> : null}
          {it.label}
        </button>
      ))}
    </div>
  )
}

export function Panel(props: {
  title?: string
  eyebrow?: string
  actions?: ReactNode
  className?: string
  style?: CSSProperties
  children?: ReactNode
}) {
  return (
    <section className={cx('cb-panel', props.className)} style={props.style}>
      {props.title || props.actions ? (
        <div className="cb-panel-head">
          <div>
            {props.eyebrow ? <div className="cb-eyebrow">{props.eyebrow}</div> : null}
            {props.title ? <h3 className="cb-h">{props.title}</h3> : null}
          </div>
          {props.actions}
        </div>
      ) : null}
      <div className="cb-panel-body">{props.children}</div>
    </section>
  )
}

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  hint?: string
  error?: string
  icon?: IconName
  suffix?: ReactNode
}

export function TextField({ label, hint, error, icon, suffix, id, ...rest }: TextFieldProps) {
  const auto = useId()
  const fieldId = id ?? auto
  const hintId = `${fieldId}-hint`
  return (
    <div className="cb-field" data-state={error ? 'error' : undefined}>
      {label ? <label htmlFor={fieldId}>{label}</label> : null}
      <div className="cb-input">
        {icon ? <Icon name={icon} /> : null}
        <input id={fieldId} aria-invalid={error ? true : undefined} aria-describedby={error || hint ? hintId : undefined} {...rest} />
        {suffix}
      </div>
      {error || hint ? (
        <div className="cb-field-hint" id={hintId}>
          {error ?? hint}
        </div>
      ) : null}
    </div>
  )
}

export function Callout(props: {
  tone?: 'info' | 'warning' | 'danger' | 'success'
  title?: ReactNode
  icon?: IconName
  actions?: ReactNode
  children?: ReactNode
}) {
  const tone = props.tone ?? 'info'
  const defaults: Record<string, IconName> = { info: 'info', warning: 'alert', danger: 'shield', success: 'check' }
  return (
    <div className={`cb-callout cb-callout-${tone}`} role={tone === 'danger' ? 'alert' : undefined}>
      <Icon name={props.icon ?? defaults[tone] ?? 'info'} />
      <div>
        {props.title ? <b>{props.title}</b> : null}
        {props.children}
        {props.actions ? <div className="cb-callout-actions">{props.actions}</div> : null}
      </div>
    </div>
  )
}

export function CheckList({ items }: { items: { status?: 'pass' | 'fail' | 'warn'; label: ReactNode; detail?: ReactNode }[] }) {
  const icon = { pass: 'check', fail: 'alert', warn: 'alert' } as const
  const text = { pass: '通过', fail: '未通过', warn: '警告' } as const
  return (
    <ul className="cb-checks">
      {items.map((it, i) => {
        const s = it.status ?? 'pass'
        return (
          <li key={i} className={`cb-check cb-check-${s}`}>
            <Icon name={icon[s]} label={text[s]} />
            <div>
              {it.label}
              {it.detail ? <small>{it.detail}</small> : null}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

export function Trace({ steps }: { steps: { status?: 'ok' | 'fail' | 'retry' | 'run' | 'wait'; label: ReactNode; time?: string }[] }) {
  const icon = { ok: 'check', fail: 'x', retry: 'refresh', run: 'loader', wait: 'more' } as const
  return (
    <ol className="cb-trace">
      {steps.map((st, i) => (
        <li key={i} className={st.status ?? 'ok'}>
          <Icon name={icon[st.status ?? 'ok']} />
          <span>{st.label}</span>
          <span className="cb-t">{st.time ?? ''}</span>
        </li>
      ))}
    </ol>
  )
}

/** 启用/停用开关：打开为品牌橙，关闭为灰。label 是读屏用的对象名，如「销售团队」。 */
export function Switch(props: { checked: boolean; onChange: (next: boolean) => void; label: string; disabled?: boolean; title?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={props.checked}
      aria-label={`${props.checked ? '停用' : '启用'}${props.label}`}
      title={props.title ?? (props.checked ? '已启用，点击停用' : '已停用，点击启用')}
      disabled={props.disabled}
      className="cb-switch"
      onClick={() => props.onChange(!props.checked)}
    >
      <span />
    </button>
  )
}

export function useToggle(initial = false): [boolean, () => void, (v: boolean) => void] {
  const [v, set] = useState(initial)
  return [v, () => set((x) => !x), set]
}
