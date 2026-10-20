import { useEffect } from 'react'
import { useAvailableDatasources } from '../api/hooks'
import { Select } from './Select'

export function DatasourcePicker(props: { value: number | null; onChange: (id: number | null) => void }) {
  const { data } = useAvailableDatasources()
  const items = data?.items
  const first = items?.[0]?.id
  const { value, onChange } = props
  useEffect(() => {
    if (value == null && first != null) onChange(first)
  }, [value, first, onChange])
  return (
    <Select
      ariaLabel="数据源"
      value={props.value}
      onChange={props.onChange}
      options={(items ?? []).map((d) => ({ value: d.id, label: `${d.type} · ${d.name}` }))}
    />
  )
}
