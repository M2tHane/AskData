import { useEffect, useRef } from 'react'
import type { CheckState } from '../lib/grid'

/** 支持半选的复选框：表下只有部分字段勾选时显示半选。 */
export function Check(props: { state: CheckState; label: string; disabled?: boolean; onChange: (on: boolean) => void }) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = props.state === 'some'
  }, [props.state])
  return <input ref={ref} type="checkbox" aria-label={props.label} disabled={props.disabled} checked={props.state === 'on'} onChange={(e) => props.onChange(e.target.checked)} />
}
