import { useState } from 'react'
import { useFreeUsers, useSaveTeam, useSaveUser, useTeamMembers, useTeams, useTeamUsers, useUsers } from '../api/hooks'
import type { TeamRead, UserRead } from '../api/types'
import { Badge, Button, Callout, Panel, Switch, TextField } from '../components/basic'
import { Dialog, DialogFooter } from '../components/Dialog'
import { MultiSelect } from '../components/MultiSelect'
import { Pager } from '../components/Pager'
import { Select } from '../components/Select'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { ROLE_LABEL } from '../lib/nav'

type Status = 'active' | 'disabled'
const FREE = 0


export function OrgPage() {
  const teams = useTeams()
  const [page, setPage] = useState(1)
  const users = useUsers(page)
  const [team, setTeam] = useState<{ id?: number; name: string; status: Status; leader: number } | null>(null)
  const [user, setUser] = useState<{ id?: number; username: string; password: string; team: number; status: Status; role: UserRead['role']; teamLabel?: string } | null>(null)
  const [managing, setManaging] = useState<number | null>(null)
  const saveTeam = useSaveTeam()
  const saveUser = useSaveUser()
  const toast = useToast()
  const failed = (title: string) => (e: unknown) => toast({ tone: 'danger', title, message: e instanceof ApiError ? describeError(e.code) : undefined })
  const toggleTeam = (t: TeamRead, on: boolean) =>
    saveTeam.mutate(
      { id: t.id, body: { name: t.name, status: on ? 'active' : 'disabled' } },
      { onSuccess: () => toast({ tone: 'success', title: `${t.name}：${on ? '已启用' : '已停用'}`, message: on ? undefined : '团队成员将无法登录使用。' }), onError: failed(`${t.name}：操作失败`) },
    )
  const toggleUser = (u: UserRead, on: boolean) =>
    saveUser.mutate(
      { id: u.id, body: { role: u.role, team_id: u.team_id, status: on ? 'active' : 'disabled', password: null } },
      { onSuccess: () => toast({ tone: 'success', title: `${u.username}：${on ? '已启用' : '已停用'}` }), onError: failed(`${u.username}：操作失败`) },
    )
  const teamName = (id: number | null) => (id == null ? null : teams.data?.items.find((t) => t.id === id)?.name ?? `团队 ${id}`)

  return (
    <div className="cb-page">
      <Panel title="团队" actions={<Button variant="primary" icon="plus" onClick={() => setTeam({ name: '', status: 'active', leader: FREE })}>添加团队</Button>}>
        <table className="cb-data cb-data-fixed">
          <thead><tr><th>名称</th><th>负责人</th><th>成员数</th><th style={{ width: 200 }} /></tr></thead>
          <tbody>
            {(teams.data?.items ?? []).map((t: TeamRead) => (
              <tr key={t.id} className={(t.status ?? 'active') === 'active' ? undefined : 'cb-row-off'}>
                <td>{t.name}</td>
                <td>{t.leader_name ?? <span className="cb-muted">未指定</span>}</td>
                <td className="cb-num">{t.member_count ?? 0}</td>
                <td style={{ textAlign: 'right' }}>
                  <span className="cb-row-actions">
                    <Button size="sm" variant="ghost" onClick={() => setManaging(t.id)}>成员</Button>
                    <Button size="sm" variant="ghost" onClick={() => setTeam({ id: t.id, name: t.name, status: t.status ?? 'active', leader: FREE })}>编辑</Button>
                    <Switch checked={(t.status ?? 'active') === 'active'} label={t.name} disabled={saveTeam.isPending} onChange={(on) => toggleTeam(t, on)} />
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {teams.data && !teams.data.items.length ? <div className="cb-muted">还没有团队。成员和授权都挂在团队下，先添加一个。</div> : null}
      </Panel>

      <Panel title="用户" actions={<Button variant="primary" icon="plus" onClick={() => setUser({ username: '', password: '', team: FREE, status: 'active', role: 'member' })}>添加用户</Button>}>
        <table className="cb-data cb-data-fixed">
          <thead><tr><th>用户名</th><th>角色</th><th>团队</th><th style={{ width: 160 }} /></tr></thead>
          <tbody>
            {(users.data?.items ?? []).map((u: UserRead) => (
              <tr key={u.id} className={u.status === 'active' ? undefined : 'cb-row-off'}>
                <td>{u.username}</td>
                <td>{ROLE_LABEL[u.role]}</td>
                <td>{u.role === 'admin' ? '—' : teamName(u.team_id) ?? <Badge>自由员工</Badge>}</td>
                <td style={{ textAlign: 'right' }}>
                  <span className="cb-row-actions">
                    <Button size="sm" variant="ghost" onClick={() => setUser({ id: u.id, username: u.username, password: '', team: u.team_id ?? FREE, status: (u.status as Status) ?? 'active', role: u.role })}>编辑</Button>
                    <Switch checked={u.status === 'active'} label={u.username} disabled={saveUser.isPending} onChange={(on) => toggleUser(u, on)} />
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pager page={page} pageSize={20} total={users.data?.total ?? 0} onPage={setPage} />
      </Panel>

      {team ? <TeamDialog value={team} onClose={() => setTeam(null)} /> : null}
      {user ? <UserDialog value={user} teams={teams.data?.items ?? []} onClose={() => setUser(null)} /> : null}
      {managing != null ? <MembersDialog teamId={managing} onClose={() => setManaging(null)} /> : null}
    </div>
  )
}

function TeamDialog(props: { value: { id?: number; name: string; status: Status; leader: number }; onClose: () => void }) {
  const [form, setForm] = useState(props.value)
  const free = useFreeUsers(!form.id)
  const save = useSaveTeam()
  const toast = useToast()
  const submit = () =>
    save.mutate(
      { id: form.id, body: form.id ? { name: form.name, status: form.status } : { name: form.name, status: 'active', leader_id: form.leader || null } },
      {
        onSuccess: () => {
          toast({ tone: 'success', title: form.id ? '团队已保存' : '团队已创建' })
          props.onClose()
        },
      },
    )
  return (
    <Dialog title={form.id ? '编辑团队' : '添加团队'} onClose={props.onClose}>
      <div className="cb-form">
        <TextField label="名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        {form.id ? null : (
          <div className="cb-field">
            <label htmlFor="team-leader">负责人</label>
            <Select
              id="team-leader"
              value={form.leader}
              onChange={(leader) => setForm({ ...form, leader })}
              options={[{ value: FREE, label: '暂不指定' }, ...(free.data?.items ?? []).map((u) => ({ value: u.id, label: u.username }))]}
            />
            <small className="cb-muted">只能从自由员工中选择；也可以先建团队，稍后再指定。</small>
          </div>
        )}
        {save.error instanceof ApiError ? <Callout tone="danger">{describeError(save.error.code)}</Callout> : null}
        <DialogFooter>
          <Button variant="ghost" onClick={props.onClose}>取消</Button>
          <Button variant="primary" disabled={!form.name.trim() || save.isPending} onClick={submit}>保存团队</Button>
        </DialogFooter>
      </div>
    </Dialog>
  )
}

function UserDialog(props: {
  value: { id?: number; username: string; password: string; team: number; status: Status; role: UserRead['role'] }
  teams: TeamRead[]
  onClose: () => void
}) {
  const [form, setForm] = useState(props.value)
  const save = useSaveUser()
  const toast = useToast()
  const editing = form.id != null
  const submit = () => {
    const team_id = form.role === 'admin' || form.team === FREE ? null : form.team
    const body = editing
      ? { role: form.role, team_id, status: form.status, password: form.password || null }
      : { username: form.username, password: form.password, role: 'member' as const, team_id, status: form.status }
    save.mutate(
      { id: form.id, body },
      {
        onSuccess: () => {
          toast({ tone: 'success', title: editing ? '用户已保存' : '用户已创建' })
          props.onClose()
        },
      },
    )
  }
  return (
    <Dialog title={editing ? '编辑用户' : '添加用户'} onClose={props.onClose}>
      <div className="cb-form">
        <TextField label="用户名" value={form.username} disabled={editing} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <TextField label="密码" type="password" value={form.password} hint={editing ? '留空表示不修改' : '至少 12 个字符'} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        {!editing ? (
          <div className="cb-field">
            <label htmlFor="user-team">团队</label>
            <Select
              id="user-team"
              value={form.team}
              onChange={(team) => setForm({ ...form, team })}
              options={[{ value: FREE, label: '自由员工（暂不加入团队）' }, ...props.teams.filter((t) => t.status === 'active').map((t) => ({ value: t.id, label: t.name }))]}
            />
            <small className="cb-muted">自由员工在被拉进团队前无法登录使用。</small>
          </div>
        ) : (
          <small className="cb-muted">团队和负责人请在团队的「成员」里调整。</small>
        )}
        {save.error instanceof ApiError ? <Callout tone="danger">{describeError(save.error.code)}</Callout> : null}
        <DialogFooter>
          <Button variant="ghost" onClick={props.onClose}>取消</Button>
          <Button variant="primary" disabled={save.isPending || !form.username || (!editing && form.password.length < 12)} onClick={submit}>保存用户</Button>
        </DialogFooter>
      </div>
    </Dialog>
  )
}

function MembersDialog(props: { teamId: number; onClose: () => void }) {
  const toast = useToast()
  const teams = useTeams()
  const team = teams.data?.items.find((t) => t.id === props.teamId)
  const members = useTeamUsers(props.teamId)
  const free = useFreeUsers()
  const ops = useTeamMembers(props.teamId)
  const [leader, setLeader] = useState<number | null>(null)
  const oops = (e: unknown) => toast({ tone: 'danger', title: '操作失败', message: e instanceof ApiError ? describeError(e.code) : undefined })
  const rows = members.data?.items ?? []
  const freeUsers = free.data?.items ?? []
  const leaderOptions = [
    ...rows.filter((u) => u.id !== team?.leader_id).map((u) => ({ value: u.id, label: u.username, hint: '本团队' })),
    ...freeUsers.map((u) => ({ value: u.id, label: u.username, hint: '自由员工' })),
  ]
  return (
    <Dialog title={`${team?.name ?? '团队'} · 成员`} size="lg" onClose={props.onClose}>
      <div className="cb-form">
        <div className="cb-field">
          <label htmlFor="team-new-leader">团队负责人</label>
          <div className="cb-row">
            <span>{team?.leader_name ?? <span className="cb-muted">未指定</span>}</span>
            <Select id="team-new-leader" ariaLabel="新负责人" placeholder="选择新负责人" value={leader} onChange={setLeader} options={leaderOptions} />
            <Button
              disabled={leader == null || ops.changeLeader.isPending}
              onClick={() =>
                leader != null &&
                ops.changeLeader.mutate(leader, {
                  onSuccess: () => {
                    setLeader(null)
                    toast({ tone: 'success', title: '负责人已更换', message: team?.leader_name ? `${team.leader_name} 已变为自由员工。` : undefined })
                  },
                  onError: oops,
                })
              }
            >
              更换负责人
            </Button>
          </div>
          <small className="cb-muted">新负责人只能来自本团队或自由员工；原负责人会变为自由员工。</small>
        </div>

        <div className="cb-toolbar">
          <strong>成员（{rows.length}）</strong>
          <span className="cb-spacer" />
          <MultiSelect
            ariaLabel="从自由员工中拉入"
            options={freeUsers.map((u) => ({ value: u.id, label: u.username }))}
            value={[]}
            emptyText="没有自由员工"
            onChange={(ids) => ops.add.mutate(ids, { onSuccess: () => toast({ tone: 'success', title: '已加入团队' }), onError: oops })}
          >
            从自由员工中拉入
          </MultiSelect>
        </div>
        <table className="cb-data cb-data-fixed">
          <thead><tr><th>用户名</th><th>角色</th><th /></tr></thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>{ROLE_LABEL[u.role]}</td>
                <td style={{ textAlign: 'right' }}>
                  {u.role === 'member' ? (
                    <Button size="sm" disabled={ops.remove.isPending} onClick={() => ops.remove.mutate(u.id, { onSuccess: () => toast({ tone: 'success', title: '已移出团队', message: `${u.username} 现在是自由员工。` }), onError: oops })}>移出团队</Button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {members.data && !rows.length ? <div className="cb-muted">团队里还没有成员。</div> : null}
        <DialogFooter><Button variant="primary" onClick={props.onClose}>完成</Button></DialogFooter>
      </div>
    </Dialog>
  )
}


