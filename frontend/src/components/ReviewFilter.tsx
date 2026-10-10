import { usePeople } from '../api/hooks'
import { Button } from './basic'
import { Select } from './Select'

export interface ReviewRange {
  userId?: number
  /** 本地日期 yyyy-mm-dd，含首尾两天。 */
  from?: string
  to?: string
}

const ALL = 0

/** 本地日期范围 → 接口用的 [since, until)：结束日整天都算在内。 */
export function rangeQuery(r: ReviewRange): { user_id?: number; since?: string; until?: string } {
  const start = (day: string) => new Date(`${day}T00:00:00`)
  let until: string | undefined
  if (r.to) {
    const end = start(r.to)
    end.setDate(end.getDate() + 1)
    until = end.toISOString()
  }
  return { user_id: r.userId, since: r.from ? start(r.from).toISOString() : undefined, until }
}

/** 查询日志和反馈共用：按用户、按日期范围筛选。 */
export function ReviewFilter(props: { value: ReviewRange; onChange: (next: ReviewRange) => void }) {
  const people = usePeople()
  const { value, onChange } = props
  const active = value.userId != null || value.from || value.to
  return (
    <div className="cb-review-filter">
      <Select
        size="sm"
        ariaLabel="按用户筛选"
        value={value.userId ?? ALL}
        onChange={(id) => onChange({ ...value, userId: id === ALL ? undefined : id })}
        options={[{ value: ALL, label: '全部用户' }, ...(people.data ?? []).map((p) => ({ value: p.id, label: p.username }))]}
      />
      <div className="cb-input cb-input-sm">
        <input type="date" aria-label="开始日期" value={value.from ?? ''} max={value.to} onChange={(e) => onChange({ ...value, from: e.target.value || undefined })} />
      </div>
      <span className="cb-muted">至</span>
      <div className="cb-input cb-input-sm">
        <input type="date" aria-label="结束日期" value={value.to ?? ''} min={value.from} onChange={(e) => onChange({ ...value, to: e.target.value || undefined })} />
      </div>
      {active ? (
        <Button size="sm" variant="ghost" onClick={() => onChange({})}>
          清除
        </Button>
      ) : null}
    </div>
  )
}

/** 用户编号 → 用户名；名单里没有的（已删除或越权）显示编号。 */
export function usePersonName() {
  const people = usePeople()
  return (id: number) => people.data?.find((p) => p.id === id)?.username ?? `用户 ${id}`
}
