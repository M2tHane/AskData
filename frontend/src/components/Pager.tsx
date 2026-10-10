import { Button } from './basic'

export function Pager(props: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const last = Math.max(1, Math.ceil(props.total / props.pageSize))
  return (
    <div className="cb-pager">
      <span className="cb-num">
        共 {props.total.toLocaleString('zh-CN')} 条 · 第 {props.page}/{last} 页
      </span>
      <Button size="sm" disabled={props.page <= 1} onClick={() => props.onPage(props.page - 1)}>
        上一页
      </Button>
      <Button size="sm" disabled={props.page >= last} onClick={() => props.onPage(props.page + 1)}>
        下一页
      </Button>
    </div>
  )
}
