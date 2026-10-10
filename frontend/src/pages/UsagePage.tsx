import { useState } from 'react'
import { useAudit, useModels, useQuota, useQuotaAlerts, useSaveQuota, useTeams, useUsage, useUsageTrend } from '../api/hooks'
import { ChartCard } from '../components/charts'
import { LEVEL_LABEL, QUOTA_LABEL, alertsOnly, pivotTrend } from '../lib/usage'
import { Badge, Button, Callout, Panel, Tabs, TextField } from '../components/basic'
import { Pager } from '../components/Pager'
import { ApiError } from '../lib/errors'
import { formatInt, formatTime } from '../lib/format'
import { Select } from '../components/Select'
import { usePersonName } from '../components/ReviewFilter'

export function UsagePage() {
  const [tab, setTab] = useState('usage')
  return (
    <div className="cb-page">
      <Tabs label="用量与审计" value={tab} onChange={setTab} items={[{ id: 'usage', label: '用量与配额' }, { id: 'audit', label: '审计日志' }]} />
      {tab === 'usage' ? <UsageTab /> : <AuditTab />}
    </div>
  )
}

function UsageTab() {
  const [page, setPage] = useState(1)
  const usage = useUsage(page)
  const rows = usage.data?.items ?? []
  const calls = rows.reduce((n, r) => n + r.calls, 0)
  const tokens = rows.reduce((n, r) => n + r.tokens_in + r.tokens_out, 0)
  const teamName = useTeamNames()
  const personName = usePersonName()
  const models = useModels()
  const modelName = (id: string | number) => models.data?.items.find((m) => String(m.id) === String(id))?.model_name ?? String(id)
  return (
    <>
      <AlertsPanel />
      <TrendPanel />
      <div className="cb-grid-stats">
        <Panel><div className="cb-stat"><b>{formatInt(calls)}</b><span>本页调用次数</span></div></Panel>
        <Panel><div className="cb-stat"><b>{formatInt(tokens)}</b><span>本页 Token</span></div></Panel>
      </div>
      <Panel title="按日用量">
        <table className="cb-data">
          <thead><tr><th>日期</th><th>团队</th><th>用户</th><th>模型</th><th className="cb-r">调用</th><th className="cb-r">输入 Token</th><th className="cb-r">输出 Token</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="cb-num">{r.date}</td>
                <td>{r.team_id == null ? <span className="cb-muted">—</span> : teamName(String(r.team_id))}</td>
                <td>{personName(r.user_id)}</td>
                <td className="cb-mono">{modelName(r.model_id)}</td>
                <td className="cb-r cb-num">{formatInt(r.calls)}</td>
                <td className="cb-r cb-num">{formatInt(r.tokens_in)}</td>
                <td className="cb-r cb-num">{formatInt(r.tokens_out)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {usage.data && !rows.length ? <div className="cb-muted">还没有用量记录。</div> : null}
        <Pager page={page} pageSize={20} total={usage.data?.total ?? 0} onPage={setPage} />
      </Panel>
      <QuotaPanel />
    </>
  )
}

function useTeamNames() {
  const teams = useTeams()
  return (key: string) => (teams.data?.items ?? []).find((t) => String(t.id) === key)?.name ?? (key ? `团队 ${key}` : '无团队')
}

function AlertsPanel() {
  const alerts = useQuotaAlerts()
  const teamName = useTeamNames()
  const items = alerts.data ?? []
  const urgent = alertsOnly(items)
  if (!items.length) return null
  return (
    <Panel title="配额使用">
      {urgent.length ? (
        <Callout tone={urgent.some((i) => i.level === 'exceeded') ? 'danger' : 'warning'}>
          {urgent.map((i) => `${teamName(String(i.team_id))}${QUOTA_LABEL[i.kind]}${i.level === 'exceeded' ? '已超限，新的提问会被拒绝' : '已用到 ' + Math.round(i.ratio * 100) + '%'}`).join('；')}。
        </Callout>
      ) : null}
      <table className="cb-data">
        <thead><tr><th>团队</th><th>项目</th><th>已用 / 上限</th><th>使用率</th></tr></thead>
        <tbody>
          {items.map((i) => (
            <tr key={`${i.team_id}-${i.kind}`}>
              <td>{teamName(String(i.team_id))}</td>
              <td>{QUOTA_LABEL[i.kind]}</td>
              <td className="cb-num">{formatInt(i.used)} / {formatInt(i.limit)}</td>
              <td>
                <div className="cb-meter-row">
                <div className="cb-meter" data-level={i.level} role="progressbar" aria-valuenow={Math.min(100, Math.round(i.ratio * 100))} aria-valuemin={0} aria-valuemax={100} aria-label={`${QUOTA_LABEL[i.kind]}使用率`}>
                  <i style={{ width: `${Math.min(100, i.ratio * 100)}%` }} />
                </div>
                <span className={`cb-meter-label cb-level-${i.level}`}>
                  {Math.round(i.ratio * 100)}%{i.level === 'ok' ? '' : ` · ${LEVEL_LABEL[i.level]}`}
                </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}

function TrendPanel() {
  const [by, setBy] = useState<'team' | 'model'>('team')
  const [days, setDays] = useState(30)
  const trend = useUsageTrend(by, days)
  const teamName = useTeamNames()
  const table = trend.data ? pivotTrend(trend.data.points, trend.data.since, trend.data.until, by === 'team' ? teamName : (k) => k || '未知模型') : { rows: [], keys: [] }
  return (
    <Panel
      title="Token 用量趋势"
      actions={
        <div className="cb-row">
          <Select ariaLabel="统计维度" value={by} onChange={setBy} options={[{ value: 'team', label: '按团队' }, { value: 'model', label: '按模型' }]} />
          <Select ariaLabel="时间范围" value={days} onChange={setDays} options={[{ value: 7, label: '近 7 天' }, { value: 30, label: '近 30 天' }, { value: 90, label: '近 90 天' }]} />
        </div>
      }
    >
      {trend.data && table.keys.length === 0 ? (
        <div className="cb-muted">这段时间没有用量记录。</div>
      ) : (
        <ChartCard spec={{ type: 'line', x: 'date', y: table.keys, stack: false, title: `每日 Token（${by === 'team' ? '按团队' : '按模型'}）` }} rows={table.rows} singleAxis />
      )}
    </Panel>
  )
}

function QuotaPanel() {
  const teams = useTeams()
  const [picked, setPicked] = useState<number | null>(null)
  const teamId = picked ?? teams.data?.items[0]?.id ?? null
  const quota = useQuota(teamId)
  const save = useSaveQuota(teamId)
  const [form, setForm] = useState<{ key: string; daily: string; monthly: string } | null>(null)
  const key = `${teamId}:${quota.dataUpdatedAt}`
  const daily = form?.key === key ? form.daily : String(quota.data?.daily_query_limit ?? '')
  const monthly = form?.key === key ? form.monthly : String(quota.data?.monthly_token_limit ?? '')
  const num = (s: string) => (s.trim() === '' ? null : Number(s))
  return (
    <Panel
      title="团队配额"
      actions={
        <Select ariaLabel="团队" value={teamId} onChange={setPicked} options={(teams.data?.items ?? []).map((t) => ({ value: t.id, label: t.name }))} />
      }
    >
      <div className="cb-form">
        <TextField label="每日查询上限" type="number" hint="留空表示不限制" value={daily} onChange={(e) => setForm({ key, daily: e.target.value, monthly })} />
        <TextField label="每月 Token 上限" type="number" hint="留空表示不限制" value={monthly} onChange={(e) => setForm({ key, daily, monthly: e.target.value })} />
        {save.error instanceof ApiError ? <Callout tone="danger">{save.error.message}</Callout> : null}
        {save.isSuccess ? <Callout tone="success">配额已保存。</Callout> : null}
        <div><Button variant="primary" disabled={save.isPending || teamId == null} onClick={() => save.mutate({ daily_query_limit: num(daily), monthly_token_limit: num(monthly) })}>保存配额</Button></div>
      </div>
    </Panel>
  )
}

function AuditTab() {
  const [page, setPage] = useState(1)
  const [action, setAction] = useState('')
  const audit = useAudit(page, action)
  return (
    <Panel title="审计日志">
      <div className="cb-toolbar">
        <TextField aria-label="操作筛选" placeholder="按操作筛选，例如 chat.ask" icon="search" value={action} onChange={(e) => { setAction(e.target.value); setPage(1) }} />
      </div>
      <table className="cb-data">
        <thead><tr><th>时间</th><th>操作人</th><th>操作</th><th>对象</th></tr></thead>
        <tbody>
          {(audit.data?.items ?? []).map((a) => (
            <tr key={a.id}>
              <td className="cb-muted">{formatTime(a.created_at)}</td>
              <td>{a.actor_id}</td>
              <td><Badge mono>{a.action}</Badge></td>
              <td className="cb-mono">{a.target_type}:{a.target_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {audit.data && !audit.data.items.length ? <div className="cb-muted">没有符合条件的记录。</div> : null}
      <Pager page={page} pageSize={20} total={audit.data?.total ?? 0} onPage={setPage} />
    </Panel>
  )
}
