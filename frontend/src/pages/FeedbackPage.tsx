import { useState } from 'react'
import { useFeedback, useHandleFeedback } from '../api/hooks'
import { Badge, Button, Panel, Tabs } from '../components/basic'
import { Pager } from '../components/Pager'
import { ReviewFilter, rangeQuery, usePersonName, type ReviewRange } from '../components/ReviewFilter'
import { formatTime } from '../lib/format'

const REASON: Record<string, string> = {
  metric_wrong: '口径不对',
  table_wrong: '选错表',
  column_wrong: '选错字段',
  empty_result: '结果为空',
  result_wrong: '结果不对',
  chart_wrong: '图表不对',
  other: '其他',
}

export function FeedbackPage() {
  const [status, setStatus] = useState('open')
  const [page, setPage] = useState(1)
  const [range, setRange] = useState<ReviewRange>({})
  const { data } = useFeedback({ status, ...rangeQuery(range), page })
  const personName = usePersonName()
  const handle = useHandleFeedback()
  return (
    <div className="cb-page">
      <Panel title="用户反馈">
        <div className="cb-toolbar">
          <Tabs
            label="处理状态"
            value={status}
            onChange={(s) => { setStatus(s); setPage(1) }}
            items={[{ id: 'open', label: '待处理' }, { id: 'handled', label: '已处理' }, { id: '', label: '全部' }]}
          />
          <span className="cb-spacer" />
          <ReviewFilter value={range} onChange={(r) => { setRange(r); setPage(1) }} />
        </div>
        <table className="cb-data">
          <thead>
            <tr><th>评价</th><th>原因</th><th>说明</th><th>问题</th><th>用户</th><th>时间</th><th /></tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((f) => (
              <tr key={f.id}>
                <td>{f.rating === 'up' ? <Badge tone="success" icon="thumbsUp">有用</Badge> : <Badge tone="danger" icon="thumbsDown">没用</Badge>}</td>
                <td>{f.reason_code ? REASON[f.reason_code] ?? f.reason_code : <span className="cb-muted">—</span>}</td>
                <td>{f.comment}</td>
                <td title={`q_${f.query_run_id}`}>{typeof f.snapshot?.question === 'string' ? f.snapshot.question : <span className="cb-mono">q_{f.query_run_id}</span>}</td>
                <td>{personName(f.user_id)}</td>
                <td className="cb-muted">{formatTime(f.created_at)}</td>
                <td style={{ textAlign: 'right' }}>
                  {f.status === 'open' ? (
                    <Button size="sm" disabled={handle.isPending} onClick={() => handle.mutate(f.id)}>标记已处理</Button>
                  ) : (
                    <Badge tone="success" icon="check">已处理</Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && !data.items.length ? <div className="cb-muted">没有反馈。</div> : null}
        <Pager page={page} pageSize={20} total={data?.total ?? 0} onPage={setPage} />
      </Panel>
    </div>
  )
}
