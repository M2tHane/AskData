import { Icon } from './Icon'
import { MultiSelect } from './MultiSelect'

export interface TeamOption {
  id: number
  name: string
}

/** 已选团队的标签（右上角 × 可移除）加「添加团队」多选下拉。 */
export function ScopeChips(props: {
  pool: TeamOption[]
  value: number[]
  onChange: (value: number[]) => void
  /** 点击 × 时先交给调用方确认；不传则直接移除。 */
  onRemove?: (id: number) => void
  label: string
  disabled?: boolean
  emptyText?: string
}) {
  const name = (id: number) => props.pool.find((t) => t.id === id)?.name ?? `团队 ${id}`
  const remove = (id: number) => (props.onRemove ? props.onRemove(id) : props.onChange(props.value.filter((v) => v !== id)))
  return (
    <div className="cb-chips" role="group" aria-label={props.label}>
      {props.value.length === 0 ? <span className="cb-muted">{props.emptyText ?? '尚未选择团队'}</span> : null}
      {props.value.map((id) => (
        <span key={id} className="cb-team-chip">
          <span>{name(id)}</span>
          <button type="button" aria-label={`移除 ${name(id)}`} disabled={props.disabled} onClick={() => remove(id)}>
            <Icon name="x" size="sm" />
          </button>
        </span>
      ))}
      <MultiSelect
        ariaLabel={`${props.label}：添加团队`}
        size="sm"
        disabled={props.disabled}
        options={props.pool.map((t) => ({ value: t.id, label: t.name }))}
        value={props.value}
        onChange={props.onChange}
        emptyText="没有可添加的团队"
      >
        添加团队
      </MultiSelect>
    </div>
  )
}
