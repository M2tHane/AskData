import type { Role, ScopeLevel } from '../api/types'

/** 每个角色只能新建、编辑自己这一级的语义和指标（与后端 creation_scope 一致）。 */
export const OWN_SCOPE: Record<Role, ScopeLevel> = { member: 'personal', leader: 'team', admin: 'platform' }

export const SCOPE_OPTIONS: Record<Role, ScopeLevel[]> = {
  member: ['personal'],
  leader: ['team'],
  admin: ['platform'],
}

export const SCOPE_NAME: Record<ScopeLevel, string> = { platform: '平台级', team: '团队级', personal: '个人级' }

/**
 * 看得到的条目里，自己这一级、且不在审核中的能改；管理员还能改团队级（保留原团队归属）。
 * 其余是继承来的定义，只读。
 */
export function canEdit(item: { scope: ScopeLevel; status: string }, role: Role): boolean {
  if (item.status === 'pending') return false
  return item.scope === OWN_SCOPE[role] || (role === 'admin' && item.scope === 'team')
}

export const STATUS_LABEL: Record<string, string> = { private: '私有', pending: '待审核', team: '团队', active: '已生效' }
