import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useFreeUsers, useSaveTeam, useSaveUser, useTeamMembers, useTeams, useTeamUsers } from '../api/hooks'
import type { TeamRead, UserRead } from '../api/types'
import { Button, Callout, Switch, Tabs, TextField } from '../components/basic'
import { ConfirmDialog, Dialog, DialogFooter } from '../components/Dialog'
import { EmptyState, ListCard, ListStatus } from '../components/ListStates'
import { HeaderActions } from '../components/PageHeader'
import { Pager, usePaged } from '../components/Pager'
import { Select } from '../components/Select'
import { useToast } from '../components/Toast'
import { ApiError, describeError } from '../lib/errors'
import { ROLE_LABEL } from '../lib/nav'

const FREE = 0
const USER_PAGE_SIZE = 10

type Tab = 'teams' | 'users'

/** 用户列表按服务端分页，每页 10 条；key 以 users 开头，保存用户后会随之刷新。 */
const useUserPage = (page: number) =>
  useQuery({
    queryKey: ['users', 'paged', page],
    queryFn: () => api<{ items: UserRead[]; total: number }>('GET', '/api/org/users', { query: { page, page_size: USER_PAGE_SIZE } }),
    placeholderData: keepPreviousData,
  })

export function OrgPage() {
  const [tab, setTab] = useState<Tab>('teams')
  const [page, setPage] = useState(1)
  const teams = useTeams()
  const users = useUserPage(page)
  const [teamDialog, setTeamDialog] = useState<'new' | number | null>(null)
  const [userDialog, setUserDialog] = useState<'new' | UserRead | null>(null)
  const [confirm, setConfirm] = useState<{ kind: 'team'; team: TeamRead } | { kind: 'user'; user: UserRead } | null>(null)
  const saveTeam = useSaveTeam()
  const saveUser = useSaveUser()
  const toast = useToast()
  const teamItems = teams.data?.items ?? []
  const paged = usePaged(teamItems)
  const userItems = users.data?.items ?? []
  const failed = (title: string) => (e: unknown) => toast({ tone: 'danger', title, message: e instanceof ApiError ? describeError(e.code) : undefined })

  const setTeamStatus = (t: TeamRead, on: boolean) =>
    saveTeam.mutate(
      { id: t.id, body: { name: t.name, status: on ? 'active' : 'disabled' } },
      {
        onSuccess: () => {
          setConfirm(null)
          toast({ tone: 'success', title: `${t.name}：${on ? '已启用' : '已停用'}`, message: on ? undefined : '团队成员将无法登录使用。' })
        },
        onError: failed(`${t.name}：操作失败`),
      },
    )
  const setUserStatus = (u: UserRead, on: boolean) =>
    saveUser.mutate(
      { id: u.id, body: { role: u.role, team_id: u.team_id, status: on ? 'active' : 'disabled', password: null } },
      {
        onSuccess: () => {
          setConfirm(null)
          toast({ tone: 'success', title: `${u.username}：${on ? '已启用' : '已停用'}`, message: on ? undefined : '这个账号将无法登录。' })
        },
        onError: failed(`${u.username}：操作失败`),
      },
    )
  const teamName = (id: number | null) => (id == null ? null : teamItems.find((t) => t.id === id)?.name ?? `团队 ${id}`)

  return (
    <div className="cb-page">
      <HeaderActions>
        {tab === 'teams' ? (
          <Button variant="primary" icon="plus" onClick={() => setTeamDialog('new')}>添加团队</Button>
        ) : (
          <Button variant="primary" icon="plus" onClick={() => setUserDialog('new')}>添加用户</Button>
        )}
      </HeaderActions>
      <Tabs
        label="团队与成员"
        value={tab}
        onChange={(t) => setTab(t as Tab)}
        items={[
          { id: 'teams', label: '团队', count: teams.data?.total ?? teamItems.length },
          { id: 'users', label: '用户', count: users.data?.total },
        ]}
      />

      {tab === 'teams' ? (
        <ListCard label="团队列表" pager={<Pager {...paged.pager} />}>
          <ListStatus
            loading={teams.isPending}
            error={teams.error}
            onRetry={() => void teams.refetch()}
            what="团队"
            count={teamItems.length}
            cols={4}
            empty={
              <EmptyState icon="users" title="还没有团队" action={<Button variant="primary" icon="plus" onClick={() => setTeamDialog('new')}>添加团队</Button>}>
                成员和授权都挂在团队下，先添加一个团队。
              </EmptyState>
            }
          >
            <table className="cb-data cb-data-fixed">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>负责人</th>
                  <th style={{ width: 96 }}>成员数</th>
                  <th style={{ width: 132 }}><span className="cb-sr">操作</span></th>
                </tr>
              </thead>
              <tbody>
                {paged.rows.map((t) => {
                  const on = (t.status ?? 'active') === 'active'
                  return (
                    <tr key={t.id} className={on ? undefined : 'cb-row-off'}>
                      <td title={t.name}>{t.name}</td>
                      <td>{t.leader_name ?? <span className="cb-muted">未指定</span>}</td>
                      <td className="cb-r">{t.member_count ?? 0}</td>
                      <td>
                        <span className="cb-actions">
                          <Button size="sm" variant="ghost" onClick={() => setTeamDialog(t.id)}>管理</Button>
                          <Switch checked={on} label={t.name} disabled={saveTeam.isPending} onChange={(next) => (next ? setTeamStatus(t, true) : setConfirm({ kind: 'team', team: t }))} />
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </ListStatus>
        </ListCard>
      ) : (
        <ListCard label="用户列表" pager={<Pager page={page} pageSize={USER_PAGE_SIZE} total={users.data?.total ?? 0} onPage={setPage} />}>
          <ListStatus
            loading={users.isPending}
            error={users.error}
            onRetry={() => void users.refetch()}
            what="用户"
            count={userItems.length}
            cols={4}
            empty={
              <EmptyState icon="user" title="还没有用户" action={<Button variant="primary" icon="plus" onClick={() => setUserDialog('new')}>添加用户</Button>}>
                添加用户并分配到团队后，他们就可以登录提问。
              </EmptyState>
            }
          >
            <table className="cb-data cb-data-fixed">
              <thead>
                <tr>
                  <th>用户名</th>
                  <th style={{ width: 140 }}>角色</th>
                  <th>团队</th>
                  <th style={{ width: 132 }}><span className="cb-sr">操作</span></th>
                </tr>
              </thead>
              <tbody>
                {userItems.map((u) => (
                  <tr key={u.id} className={u.status === 'active' ? undefined : 'cb-row-off'}>
                    <td title={u.username}>{u.username}</td>
                    <td>{ROLE_LABEL[u.role]}</td>
                    <td>{u.role === 'admin' ? <span className="cb-muted">—</span> : teamName(u.team_id) ?? <span className="cb-muted">自由员工</span>}</td>
                    <td>
                      <span className="cb-actions">
                        <Button size="sm" variant="ghost" onClick={() => setUserDialog(u)}>编辑</Button>
                        <Switch checked={u.status === 'active'} label={u.username} disabled={saveUser.isPending} onChange={(next) => (next ? setUserStatus(u, true) : setConfirm({ kind: 'user', user: u }))} />
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListStatus>
        </ListCard>
      )}

      {teamDialog === 'new' ? <NewTeamDialog onClose={() => setTeamDialog(null)} /> : null}
      {typeof teamDialog === 'number' ? <ManageTeamDialog teamId={teamDialog} onClose={() => setTeamDialog(null)} /> : null}
      {userDialog === 'new' ? <NewUserDialog teams={teamItems} onClose={() => setUserDialog(null)} /> : null}
      {userDialog && userDialog !== 'new' ? <EditUserDialog user={userDialog} teamName={teamName(userDialog.team_id)} onClose={() => setUserDialog(null)} /> : null}
      {confirm?.kind === 'team' ? (
        <ConfirmDialog title={`停用「${confirm.team.name}」`} confirmLabel="停用团队" danger busy={saveTeam.isPending} onConfirm={() => setTeamStatus(confirm.team, false)} onClose={() => setConfirm(null)}>
          停用后，团队成员将无法登录使用，数据和授权会保留。
        </ConfirmDialog>
      ) : null}
      {confirm?.kind === 'user' ? (
        <ConfirmDialog title={`停用「${confirm.user.username}」`} confirmLabel="停用用户" danger busy={saveUser.isPending} onConfirm={() => setUserStatus(confirm.user, false)} onClose={() => setConfirm(null)}>
          停用后，这个账号将无法登录，历史记录会保留。
        </ConfirmDialog>
      ) : null}
    </div>
  )
}

function NewTeamDialog(props: { onClose: () => void }) {
  const [name, setName] = useState('')
  const [leader, setLeader] = useState(FREE)
  const free = useFreeUsers()
  const save = useSaveTeam()
  const toast = useToast()
  const submit = () =>
    save.mutate(
      { body: { name: name.trim(), status: 'active', leader_id: leader || null } },
      {
        onSuccess: () => {
          toast({ tone: 'success', title: `团队「${name.trim()}」已创建` })
          props.onClose()
        },
      },
    )
  return (
    <Dialog title="添加团队" onClose={props.onClose}>
      <div className="cb-form">
        <TextField label="名称" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="cb-field">
          <label htmlFor="team-leader">负责人</label>
          <Select
            id="team-leader"
            value={leader}
            onChange={setLeader}
            options={[{ value: FREE, label: '暂不指定' }, ...(free.data?.items ?? []).map((u) => ({ value: u.id, label: u.username }))]}
          />
          <div className="cb-field-hint">只能从自由员工中选择；也可以先建团队，稍后在「管理」里指定。</div>
        </div>
        {save.error instanceof ApiError ? <Callout tone="danger">{describeError(save.error.code)}</Callout> : null}
      </div>
      <DialogFooter>
        <Button onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={!name.trim() || save.isPending} onClick={submit}>保存团队</Button>
      </DialogFooter>
    </Dialog>
  )
}

/** 团队的全部管理动作都在这一个对话框里：改名、换负责人、添加成员、设为负责人、移出团队。按钮同一尺寸。 */
function ManageTeamDialog(props: { teamId: number; onClose: () => void }) {
  const toast = useToast()
  const teams = useTeams()
  const team = teams.data?.items.find((t) => t.id === props.teamId)
  const members = useTeamUsers(props.teamId)
  const free = useFreeUsers()
  const ops = useTeamMembers(props.teamId)
  const rename = useSaveTeam()
  const [name, setName] = useState<string | null>(null)
  const [removing, setRemoving] = useState<UserRead | null>(null)
  const oops = (e: unknown) => toast({ tone: 'danger', title: '操作失败', message: e instanceof ApiError ? describeError(e.code) : undefined })
  const rows = members.data?.items ?? []
  const freeUsers = free.data?.items ?? []
  const leaderName = team?.leader_name
  const current = name ?? team?.name ?? ''
  const changeLeader = (u: { id: number; username: string }) => {
    if (u.id === team?.leader_id) return
    ops.changeLeader.mutate(u.id, {
      onSuccess: () => toast({ tone: 'success', title: `${u.username} 已成为负责人`, message: leaderName ? `${leaderName} 变为自由员工。` : undefined }),
      onError: oops,
    })
  }
  const leaderOptions = [
    ...rows.filter((u) => u.role !== 'admin').map((u) => ({ value: u.id, label: u.username, hint: '本团队' })),
    ...freeUsers.map((u) => ({ value: u.id, label: u.username, hint: '自由员工' })),
  ]
  const pick = (id: number) => {
    const u = [...rows, ...freeUsers].find((x) => x.id === id)
    if (u) changeLeader(u)
  }
  return (
    <>
    <Dialog title={`管理团队 · ${team?.name ?? ''}`} size="lg" onClose={props.onClose}>
      <div className="cb-form cb-org-manage">
        <div className="cb-field">
          <label htmlFor="team-name">团队名称</label>
          <div className="cb-org-inline">
            <TextField id="team-name" aria-label="团队名称" value={current} onChange={(e) => setName(e.target.value)} />
            <Button
              disabled={!team || !current.trim() || current.trim() === team.name || rename.isPending}
              onClick={() =>
                team &&
                rename.mutate(
                  { id: team.id, body: { name: current.trim(), status: team.status ?? 'active' } },
                  { onSuccess: () => { setName(null); toast({ tone: 'success', title: '团队名称已保存' }) }, onError: oops },
                )
              }
            >
              保存名称
            </Button>
          </div>
        </div>
        <div className="cb-field">
          <label htmlFor="team-leader-pick">团队负责人</label>
          <Select id="team-leader-pick" placeholder="未指定，选择负责人" value={team?.leader_id ?? null} disabled={ops.changeLeader.isPending} onChange={pick} options={leaderOptions} />
          <div className="cb-field-hint">在下拉里选择即可更换；新负责人来自本团队或自由员工，原负责人会变为自由员工。</div>
        </div>
        <div className="cb-org-bar">
          <strong>成员（{rows.length}）</strong>
          <Select
            ariaLabel="添加成员"
            placeholder={freeUsers.length ? '添加成员' : '没有可添加的自由员工'}
            value={null}
            disabled={!freeUsers.length || ops.add.isPending}
            options={freeUsers.map((u) => ({ value: u.id, label: u.username, hint: '自由员工' }))}
            onChange={(id) => {
              const u = freeUsers.find((x) => x.id === id)
              ops.add.mutate([id], { onSuccess: () => toast({ tone: 'success', title: `${u?.username ?? '成员'} 已加入团队` }), onError: oops })
            }}
          />
        </div>
        <div className="cb-org-members">
          <table className="cb-data cb-data-fixed">
            <thead>
              <tr><th>用户名</th><th style={{ width: 120 }}>角色</th><th style={{ width: 232 }}><span className="cb-sr">操作</span></th></tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id}>
                  <td title={u.username}>{u.username}</td>
                  <td>{ROLE_LABEL[u.role]}</td>
                  <td>
                    {u.role === 'member' ? (
                      <span className="cb-actions">
                        <Button disabled={ops.changeLeader.isPending} onClick={() => changeLeader(u)}>设为负责人</Button>
                        <Button disabled={ops.remove.isPending} onClick={() => setRemoving(u)}>移出团队</Button>
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {members.data && !rows.length ? <div className="cb-state"><p>团队里还没有成员，从上面的「添加成员」里选择。</p></div> : null}
        </div>
      </div>
      <DialogFooter>
        <Button variant="primary" onClick={props.onClose}>完成</Button>
      </DialogFooter>
    </Dialog>
      {removing ? (
        <ConfirmDialog
          title={`移出「${removing.username}」`}
          confirmLabel="移出团队"
          danger
          busy={ops.remove.isPending}
          onClose={() => setRemoving(null)}
          onConfirm={() =>
            ops.remove.mutate(removing.id, {
              onSuccess: () => {
                setRemoving(null)
                toast({ tone: 'success', title: `${removing.username} 已移出团队`, message: '现在是自由员工，被拉进团队前无法登录使用。' })
              },
              onError: oops,
            })
          }
        >
          移出后，{removing.username} 会变为自由员工，被拉进团队前无法登录使用。
        </ConfirmDialog>
      ) : null}
    </>
  )
}

function NewUserDialog(props: { teams: TeamRead[]; onClose: () => void }) {
  const [form, setForm] = useState<{ username: string; password: string; role: 'member' | 'admin'; team: number }>({ username: '', password: '', role: 'member', team: FREE })
  const save = useSaveUser()
  const toast = useToast()
  const submit = () =>
    save.mutate(
      { body: { username: form.username.trim(), password: form.password, role: form.role, team_id: form.role === 'admin' || form.team === FREE ? null : form.team, status: 'active' } },
      {
        onSuccess: () => {
          toast({ tone: 'success', title: `用户「${form.username.trim()}」已创建` })
          props.onClose()
        },
      },
    )
  return (
    <Dialog title="添加用户" onClose={props.onClose}>
      <div className="cb-form">
        <TextField label="用户名" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <TextField label="初始密码" type="password" autoComplete="new-password" hint="至少 12 个字符" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <div className="cb-field">
          <label htmlFor="user-role">角色</label>
          <Select id="user-role" value={form.role} onChange={(role) => setForm({ ...form, role })} options={[{ value: 'member', label: ROLE_LABEL.member }, { value: 'admin', label: ROLE_LABEL.admin }]} />
          <div className="cb-field-hint">团队负责人在团队的「管理」里指定。</div>
        </div>
        <div className="cb-field">
          <label htmlFor="user-team">团队</label>
          <Select
            id="user-team"
            value={form.role === 'admin' ? FREE : form.team}
            disabled={form.role === 'admin'}
            onChange={(team) => setForm({ ...form, team })}
            options={[{ value: FREE, label: form.role === 'admin' ? '管理员不属于团队' : '自由员工（暂不加入团队）' }, ...props.teams.filter((t) => t.status === 'active').map((t) => ({ value: t.id, label: t.name }))]}
          />
          <div className="cb-field-hint">自由员工在被拉进团队前无法登录使用。</div>
        </div>
        {save.error instanceof ApiError ? <Callout tone="danger">{describeError(save.error.code)}</Callout> : null}
      </div>
      <DialogFooter>
        <Button onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={save.isPending || !form.username.trim() || form.password.length < 12} onClick={submit}>保存用户</Button>
      </DialogFooter>
    </Dialog>
  )
}

function EditUserDialog(props: { user: UserRead; teamName: string | null; onClose: () => void }) {
  const u = props.user
  const [role, setRole] = useState(u.role)
  const [password, setPassword] = useState('')
  const save = useSaveUser()
  const toast = useToast()
  const isLeader = u.role === 'leader'
  const dirty = role !== u.role || password !== ''
  const submit = () =>
    save.mutate(
      { id: u.id, body: { role, team_id: role === 'admin' ? null : u.team_id, status: u.status === 'active' ? 'active' : 'disabled', password: password || null } },
      {
        onSuccess: () => {
          toast({ tone: 'success', title: `用户「${u.username}」已保存`, message: password ? '密码已重置，请告知对方新密码。' : undefined })
          props.onClose()
        },
      },
    )
  return (
    <Dialog title="编辑用户" onClose={props.onClose}>
      <div className="cb-form">
        <TextField label="用户名" value={u.username} disabled readOnly />
        <TextField label="团队" value={u.role === 'admin' ? '管理员不属于团队' : props.teamName ?? '自由员工'} disabled readOnly hint="团队在团队的「管理」里调整" />
        <div className="cb-field">
          <label htmlFor="user-role-edit">角色</label>
          <Select
            id="user-role-edit"
            value={role}
            disabled={isLeader}
            onChange={setRole}
            options={isLeader ? [{ value: 'leader', label: ROLE_LABEL.leader }] : [{ value: 'member', label: ROLE_LABEL.member }, { value: 'admin', label: ROLE_LABEL.admin }]}
          />
          {isLeader ? <div className="cb-field-hint">负责人在团队的「管理」里更换。</div> : null}
        </div>
        <TextField label="重置密码" type="password" autoComplete="new-password" hint="留空表示不修改；填写后新密码至少 12 个字符" value={password} onChange={(e) => setPassword(e.target.value)} />
        {save.error instanceof ApiError ? <Callout tone="danger">{describeError(save.error.code)}</Callout> : null}
      </div>
      <DialogFooter>
        <Button onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={save.isPending || !dirty || (password !== '' && password.length < 12)} onClick={submit}>保存用户</Button>
      </DialogFooter>
    </Dialog>
  )
}
