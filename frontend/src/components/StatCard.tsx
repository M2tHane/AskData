import type { ReactNode } from 'react'
import { Icon } from './Icon'

/** 统计卡：标签在上、24px 数字在下；完整值或口径说明放在 title 提示里。 */
export function StatCard(props: { label: string; value: ReactNode; unit?: string; title?: string; help?: string; tone?: 'success' | 'danger' }) {
  return (
    <div className={props.tone ? `cb-stat cb-stat-${props.tone}` : 'cb-stat'} title={props.title}>
      <span className="cb-stat-label">
        {props.label}
        {props.help ? (
          <span title={props.help} aria-label={props.help} role="img">
            <Icon name="help" />
          </span>
        ) : null}
      </span>
      <span className="cb-stat-value">
        {props.value}
        {props.unit ? <small>{props.unit}</small> : null}
      </span>
    </div>
  )
}

/** 一行 3–4 张统计卡。 */
export function Stats(props: { children: ReactNode; label?: string }) {
  return (
    <div className="cb-grid-stats" role="group" aria-label={props.label ?? '统计'}>
      {props.children}
    </div>
  )
}
