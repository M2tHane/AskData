import type { NavGroup } from '../components/basic'
import type { Role } from '../api/types'

export interface Route {
  id: string
  path: string
  label: string
  group: string
  icon: NonNullable<NavGroup['items'][number]['icon']>
  roles: Role[]
}

const ALL: Role[] = ['admin', 'leader', 'member']

/** 导航按角色裁剪：没有权限的入口直接不渲染。 */
export const ROUTES: Route[] = [
  { id: 'chat', path: '/chat', label: '对话', group: '查数', icon: 'chat', roles: ALL },
  { id: 'datasources', path: '/datasources', label: '数据源', group: '数据', icon: 'database', roles: ['admin'] },
  { id: 'semantic', path: '/semantic', label: '语义与别名', group: '数据', icon: 'book', roles: ALL },
  { id: 'metrics', path: '/metrics', label: '指标', group: '数据', icon: 'sigma', roles: ALL },
  { id: 'permissions', path: '/permissions', label: '权限', group: '治理', icon: 'shield', roles: ['leader'] },
  { id: 'feedback', path: '/feedback', label: '反馈', group: '治理', icon: 'flag', roles: ['admin', 'leader'] },
  { id: 'logs', path: '/logs', label: '查询日志', group: '治理', icon: 'log', roles: ['admin', 'leader'] },
  { id: 'eval', path: '/eval', label: '评测', group: '治理', icon: 'flask', roles: ['admin', 'leader'] },
  { id: 'org', path: '/org', label: '团队与成员', group: '系统', icon: 'users', roles: ['admin'] },
  { id: 'models', path: '/models', label: '模型配置', group: '系统', icon: 'cpu', roles: ['admin', 'leader'] },
  { id: 'prompts', path: '/prompts', label: '提示词', group: '系统', icon: 'sparkle', roles: ['admin'] },
  { id: 'usage', path: '/usage', label: '用量与审计', group: '系统', icon: 'log', roles: ['admin'] },
]

export function navFor(role: Role): { groups: NavGroup[]; routes: Route[] } {
  const routes = ROUTES.filter((r) => r.roles.includes(role))
  const groups: NavGroup[] = []
  for (const r of routes) {
    let g = groups.find((x) => x.label === r.group)
    if (!g) groups.push((g = { label: r.group, items: [] }))
    g.items.push({ id: r.id, label: r.label, icon: r.icon })
  }
  return { groups, routes }
}

export const ROLE_LABEL: Record<Role, string> = { admin: '管理员', leader: '团队负责人', member: '员工' }
