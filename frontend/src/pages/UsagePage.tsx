import { useState } from 'react'
import { keepPreviousData, useIsFetching, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useModels, useQuota, useQuotaAlerts, useSaveQuota, useTeams, useUsageTrend } from '../api/hooks'
import type { AuditView, Page, UsageView } from '../api/types'
import { Badge, Button, Callout, Panel, Segmented, Tabs, TextField } from '../components/basic'
import { ChartCard } from '../components/charts'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { HeaderActions } from '../components/PageHeader'
import { Pager } from '../components/Pager'
import { ReviewFilter, rangeQuery, usePersonName, type ReviewRange } from '../components/ReviewFilter'
import { Select } from '../components/Select'
import { StatCard, Stats } from '../components/StatCard'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { formatInt, formatTime } from '../lib/format'
import { LEVEL_LABEL, QUOTA_LABEL, pivotTrend } from '../lib/usage'
import { compactNumber, periodTotals } from '../lib/usageStats'

const ROWS = 10

type Days = 7 | 30 | 90
type By = 'team' | 'model'

export function UsagePage() {
  const [tab, setTab] = useState('usage')
  const qc = useQueryClient()
  const busy = useIsFetching() > 0
  return (
    <div className="cb-page">
      <HeaderActions>
        <Button icon="refresh" disabled={busy} onClick={() => void Promise.all(['usage', 'audit', 'quota'].map((k) => qc.invalidateQueries({ queryKey: [k] })))}>刷新</Button>
      </HeaderActions>
      <Tabs label="用量与审计" value={tab} onChange={setTab} items={[{ id: 'usage', label: '用量与配额' }, { id: 'audit', label: '审计日志' }]} />
      {tab === 'usage' ? <UsageTab /> : <AuditTab />}
    </div>
  )
}

function useTeamNames() {
  const teams = useTeams()
  return (key: string) => (teams.data?.items ?? []).find((t) => String(t.id) === key)?.name ?? (key ? `团队 ${key}` : '无团队')
}

function useModelName() {
  const models = useModels()
  return (id: string | number) => models.data?.items.find((m) => String(m.id) === String(id))?.model_name ?? String(id)
}

function UsageTab() {
  const [days, setDays] = useState<Days>(30)
  const [by, setBy] = useState<By>('team')
  const [section, setSection] = useState('quota')
  const trend = useUsageTrend(by, days)
  const totals = periodTotals(trend.data?.points ?? [])
  const teamName = useTeamNames()
  const modelName = useModelName()
  const table = trend.data ? pivotTrend(trend.data.points, trend.data.since, trend.data.until, by === 'team' ? teamName : (k) => (k ? modelName(k) : '未知模型')) : { rows: [], keys: [] }
  const periodLabel = `近 ${days} 天`
  return (
    <>
      <div className="cb-filters">
        <Segmented
          label="统计时间范围"
          value={String(days) as '7' | '30' | '90'}
          onChange={(v) => setDays(Number(v) as Days)}
          items={[{ id: '7', label: '近 7 天' }, { id: '30', label: '近 30 天' }, { id: '90', label: '近 90 天' }]}
        />
        <span className="cb-spacer" />
        <Segmented label="统计维度" value={by} onChange={setBy} items={[{ id: 'team', label: '按团队' }, { id: 'model', label: '按模型' }]} />
      </div>
      <Stats label={`${periodLabel}用量`}>
        <StatCard label={`${periodLabel}调用`} value={compactNumber(totals.calls)} title={formatInt(totals.calls)} />
        <StatCard label={`${periodLabel} Token`} value={compactNumber(totals.tokens)} title={formatInt(totals.tokens)} />
        <StatCard label="平均每次 Token" value={compactNumber(totals.perCall)} title={formatInt(totals.perCall)} help="Token 总量除以调用次数" />
      </Stats>
      <div className="cb-usage-chart">
        {trend.error ? (
          <div className="cb-state cb-state-danger cb-panel" role="alert">
            <b>趋势加载失败</b>
            <p>稍后重试，已有数据不受影响。</p>
            <div className="cb-row"><Button icon="refresh" onClick={() => void trend.refetch()}>重试</Button></div>
          </div>
        ) : trend.data && table.keys.length === 0 ? (
          <div className="cb-panel"><EmptyState icon="chart" title="这段时间没有用量">有人提问后，这里会按{by === 'team' ? '团队' : '模型'}显示每天的 Token。</EmptyState></div>
        ) : trend.data ? (
          <ChartCard spec={{ type: 'line', x: 'date', y: table.keys, stack: false, title: `Token 用量趋势（${by === 'team' ? '按团队' : '按模型'}）` }} rows={table.rows} singleAxis />
        ) : (
          <div className="cb-panel cb-state"><p>加载中</p></div>
        )}
      </div>
      <Tabs label="配额与明细" value={section} onChange={setSection} items={[{ id: 'quota', label: '配额使用' }, { id: 'daily', label: '按日用量' }, { id: 'team', label: '团队配额' }]} />
      {section === 'quota' ? <QuotaUsage /> : section === 'daily' ? <DailyUsage days={days} /> : <TeamQuota />}
    </>
  )
}

function QuotaUsage() {
  const alerts = useQuotaAlerts()
  const teamName = useTeamNames()
  const items = alerts.data ?? []
  return (
    <section className="cb-panel" aria-label="配额使用">
      <ListStatus
        loading={alerts.isPending}
        error={alerts.error}
        onRetry={() => void alerts.refetch()}
        what="配额使用"
        count={items.length}
        cols={4}
        empty={<EmptyState icon="chart" title="还没有设置配额">在「团队配额」里给团队设置每日查询或每月 Token 上限后，这里会显示使用率。</EmptyState>}
      >
        <table className="cb-data cb-data-fixed">
          <thead>
            <tr><th style={{ width: '20%' }}>团队</th><th style={{ width: '16%' }}>项目</th><th style={{ width: '22%' }}>已用 / 上限</th><th>使用率</th></tr>
          </thead>
          <tbody>
            {items.map((i) => {
              const pct = Math.round(i.ratio * 100)
              const fmt = i.kind === 'monthly_tokens' ? compactNumber : formatInt
              return (
                <tr key={`${i.team_id}-${i.kind}`}>
                  <td title={teamName(String(i.team_id))}>{teamName(String(i.team_id))}</td>
                  <td>{QUOTA_LABEL[i.kind]}</td>
                  <td className="cb-r" title={`${formatInt(i.used)} / ${formatInt(i.limit)}`}>{fmt(i.used)} / {fmt(i.limit)}</td>
                  <td>
                    <div className="cb-quota">
                      <div className="cb-quota-bar" data-level={i.level} role="progressbar" aria-valuenow={Math.min(100, pct)} aria-valuemin={0} aria-valuemax={100} aria-label={`${teamName(String(i.team_id))}${QUOTA_LABEL[i.kind]}使用率`}>
                        <i style={{ width: `${Math.min(100, i.ratio * 100)}%` }} />
                      </div>
                      <span className="cb-quota-label" data-level={i.level}>{pct}%{i.level === 'ok' ? '' : ` · ${LEVEL_LABEL[i.level]}`}</span>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </ListStatus>
    </section>
  )
}

/** 所选时间段内的按日明细，服务端分页每页 10 条。 */
const useUsageRows = (page: number, days: number) =>
  useQuery({
    queryKey: ['usage', 'rows', days, page],
    queryFn: () => {
      const until = new Date()
      const since = new Date(until.getTime() - (days - 1) * 86_400_000)
      const day = (d: Date) => d.toISOString().slice(0, 10)
      return api<Page<UsageView>>('GET', '/api/usage', { query: { page, page_size: ROWS, since: day(since), until: day(until) } })
    },
    placeholderData: keepPreviousData,
  })

function DailyUsage(props: { days: number }) {
  const [page, setPage] = useState(1)
  const usage = useUsageRows(page, props.days)
  const rows = usage.data?.items ?? []
  const teamName = useTeamNames()
  const personName = usePersonName()
  const modelName = useModelName()
  return (
    <section className="cb-panel" aria-label="按日用量">
      <ListStatus
        loading={usage.isPending}
        error={usage.error}
        onRetry={() => void usage.refetch()}
        what="用量"
        count={rows.length}
        cols={7}
        empty={<EmptyState icon="chart" title="这段时间没有用量记录">近 {props.days} 天内还没有人提问。</EmptyState>}
      >
        <table className="cb-data cb-data-fixed">
          <thead>
            <tr>
              <th style={{ width: 112 }}>日期</th><th>团队</th><th>用户</th><th>模型</th>
              <th style={{ width: 80 }}>调用</th><th style={{ width: 108 }}>输入 Token</th><th style={{ width: 108 }}>输出 Token</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="cb-r">{r.date}</td>
                <td>{r.team_id == null ? <span className="cb-muted">—</span> : teamName(String(r.team_id))}</td>
                <td>{personName(r.user_id)}</td>
                <td className="cb-m" title={modelName(r.model_id)}>{modelName(r.model_id)}</td>
                <td className="cb-r">{formatInt(r.calls)}</td>
                <td className="cb-r" title={formatInt(r.tokens_in)}>{compactNumber(r.tokens_in)}</td>
                <td className="cb-r" title={formatInt(r.tokens_out)}>{compactNumber(r.tokens_out)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ListStatus>
      <Pager page={page} pageSize={ROWS} total={usage.data?.total ?? 0} onPage={setPage} />
    </section>
  )
}

function TeamQuota() {
  const teams = useTeams()
  const toast = useToast()
  const [picked, setPicked] = useState<number | null>(null)
  const teamId = picked ?? teams.data?.items[0]?.id ?? null
  const quota = useQuota(teamId)
  const save = useSaveQuota(teamId)
  const [form, setForm] = useState<{ key: string; daily: string; monthly: string } | null>(null)
  const key = `${teamId}:${quota.dataUpdatedAt}`
  const daily = form?.key === key ? form.daily : String(quota.data?.daily_query_limit ?? '')
  const monthly = form?.key === key ? form.monthly : String(quota.data?.monthly_token_limit ?? '')
  const num = (s: string) => (s.trim() === '' ? null : Number(s))
  const invalid = [daily, monthly].some((s) => s.trim() !== '' && (!Number.isFinite(Number(s)) || Number(s) < 0))
  const teamLabel = teams.data?.items.find((t) => t.id === teamId)?.name ?? ''
  return (
    <Panel
      title="团队配额"
      actions={<Select ariaLabel="团队" value={teamId} onChange={setPicked} options={(teams.data?.items ?? []).map((t) => ({ value: t.id, label: t.name }))} />}
    >
      {teams.data && !teams.data.items.length ? (
        <EmptyState icon="users" title="还没有团队">先在「团队与成员」里添加团队，再设置配额。</EmptyState>
      ) : (
        <div className="cb-form">
          <TextField label="每日查询上限" type="number" min={0} hint="留空表示不限制；超过后新的提问会被拒绝" value={daily} onChange={(e) => setForm({ key, daily: e.target.value, monthly })} />
          <TextField label="每月 Token 上限" type="number" min={0} hint="留空表示不限制；超过后新的提问会被拒绝" value={monthly} onChange={(e) => setForm({ key, daily, monthly: e.target.value })} />
          {save.error instanceof ApiError ? <Callout tone="danger">{describeError(save.error.code)}</Callout> : null}
          <div>
            <Button
              variant="primary"
              disabled={save.isPending || teamId == null || invalid}
              onClick={() =>
                save.mutate(
                  { daily_query_limit: num(daily), monthly_token_limit: num(monthly) },
                  { onSuccess: () => toast({ tone: 'success', title: `${teamLabel}：配额已保存` }) },
                )
              }
            >
              保存配额
            </Button>
          </div>
        </div>
      )}
    </Panel>
  )
}

function AuditTab() {
  const [page, setPage] = useState(1)
  const [action, setAction] = useState('')
  const [range, setRange] = useState<ReviewRange>({})
  const personName = usePersonName()
  const q = rangeQuery(range)
  // 审计日志接口支持 actor_id / action / since / until；复用查询日志的筛选条，把「用户」当作操作人。
  const audit = useQuery({
    queryKey: ['audit', 'filtered', page, action, q.user_id, q.since, q.until],
    queryFn: () => api<Page<AuditView>>('GET', '/api/audit', { query: { page, page_size: ROWS, action: action.trim() || undefined, actor_id: q.user_id, since: q.since, until: q.until } }),
    placeholderData: keepPreviousData,
  })
  const items = audit.data?.items ?? []
  const filtered = !!action.trim() || range.userId != null || !!range.from || !!range.to
  const clear = () => {
    setAction('')
    setRange({})
    setPage(1)
  }
  return (
    <ListCard
      label="审计日志"
      pager={<Pager page={page} pageSize={ROWS} total={audit.data?.total ?? 0} onPage={setPage} />}
      toolbar={
        <>
          <div className="cb-search">
            <TextField aria-label="按操作筛选" placeholder="按操作筛选，例如 chat.ask" icon="search" value={action} onChange={(e) => { setAction(e.target.value); setPage(1) }} />
          </div>
          <ReviewFilter value={range} onChange={(r) => { setRange(r); setPage(1) }} />
        </>
      }
    >
      <ListStatus
        loading={audit.isPending}
        error={audit.error}
        onRetry={() => void audit.refetch()}
        what="审计日志"
        count={items.length}
        filtered={filtered}
        query={action.trim() || undefined}
        onClear={clear}
        cols={4}
        empty={<EmptyState icon="log" title="还没有审计记录">用户登录、提问和管理员的配置变更都会记在这里。</EmptyState>}
      >
        <table className="cb-data cb-data-fixed">
          <thead>
            <tr><th style={{ width: 112 }}>时间</th><th style={{ width: '20%' }}>操作人</th><th>操作</th><th>对象</th></tr>
          </thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.id}>
                <td className="cb-r" title={a.created_at}>{formatTime(a.created_at)}</td>
                <td>{personName(a.actor_id)}</td>
                <td><Badge mono>{a.action}</Badge></td>
                <td className="cb-m" title={`${a.target_type}:${a.target_id}`}>{a.target_type}:{a.target_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ListStatus>
    </ListCard>
  )
}
