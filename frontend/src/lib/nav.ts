import type { IconName } from '../components/Icon'
import type { Role } from '../api/types'
import { FEATURES } from './features'

export interface Route {
  id: string
  path: string
  label: string
  /** top：业务入口，按分组排在上面；bottom：治理与系统入口，不分组、平铺沉底。 */
  placement: 'top' | 'bottom'
  /** 只用于 top 的分组名。 */
  group?: string
  icon: IconName
  roles: Role[]
  /** 受功能开关控制的入口。 */
  enabled?: boolean
}

export interface NavGroup {
  label: string
  items: Route[]
}

const ALL: Role[] = ['admin', 'leader', 'member']

/** 导航按角色裁剪：没有权限的入口直接不渲染。顺序即侧栏顺序。 */
export const ROUTES: Route[] = [
  { id: 'chat', path: '/chat', label: '对话', placement: 'top', group: '查数', icon: 'chat', roles: ALL },
  { id: 'dashboard', path: '/dashboard', label: '我的看板', placement: 'top', group: '查数', icon: 'grid', roles: ALL, enabled: FEATURES.dashboard },
  { id: 'datasources', path: '/datasources', label: '数据源', placement: 'top', group: '数据', icon: 'database', roles: ['admin'] },
  { id: 'semantic', path: '/semantic', label: '语义与别名', placement: 'top', group: '数据', icon: 'book', roles: ALL },
  { id: 'metrics', path: '/metrics', label: '指标', placement: 'top', group: '数据', icon: 'sigma', roles: ALL },
  { id: 'feedback', path: '/feedback', label: '反馈', placement: 'bottom', icon: 'flag', roles: ['admin', 'leader'] },
  { id: 'logs', path: '/logs', label: '查询日志', placement: 'bottom', icon: 'log', roles: ['admin', 'leader'] },
  { id: 'eval', path: '/eval', label: '评测', placement: 'bottom', icon: 'flask', roles: ['admin', 'leader'] },
  { id: 'permissions', path: '/permissions', label: '权限', placement: 'bottom', icon: 'shield', roles: ['leader'] },
  { id: 'org', path: '/org', label: '团队与成员', placement: 'bottom', icon: 'users', roles: ['admin'] },
  { id: 'mcp', path: '/mcp-clients', label: 'MCP 接入', placement: 'bottom', icon: 'plug', roles: ['admin'] },
  { id: 'models', path: '/models', label: '模型配置', placement: 'bottom', icon: 'cpu', roles: ['admin', 'leader'] },
  { id: 'prompts', path: '/prompts', label: '提示词', placement: 'bottom', icon: 'sparkle', roles: ['admin'] },
  { id: 'usage', path: '/usage', label: '用量与审计', placement: 'bottom', icon: 'chart', roles: ['admin'] },
]

export function navFor(role: Role): { groups: NavGroup[]; bottom: Route[]; routes: Route[] } {
  const routes = ROUTES.filter((r) => r.roles.includes(role) && r.enabled !== false)
  const groups: NavGroup[] = []
  for (const r of routes.filter((x) => x.placement === 'top')) {
    let g = groups.find((x) => x.label === r.group)
    if (!g) groups.push((g = { label: r.group ?? '', items: [] }))
    g.items.push(r)
  }
  return { groups, bottom: routes.filter((r) => r.placement === 'bottom'), routes }
}

export const ROLE_LABEL: Record<Role, string> = { admin: '管理员', leader: '团队负责人', member: '员工' }
