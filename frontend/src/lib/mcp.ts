import type { ClientColumnWrite, ClientGrantsRead, MaskRule, McpClientRead, TableRead } from '../api/types'
import type { CheckState } from './grid'

/** 字段的脱敏方式；none 表示原样返回。 */
export type MaskChoice = 'none' | 'phone' | 'id_card' | 'full'

export const MASK_LABEL: Record<MaskChoice, string> = { none: '不脱敏', phone: '手机号', id_card: '身份证号', full: '全部隐藏' }

/** 接入的可查范围：列 id → 脱敏方式。不在表里的列就是没授权。 */
export type GrantState = Record<number, MaskChoice>

export function grantState(read: ClientGrantsRead | undefined): GrantState {
  const state: GrantState = {}
  for (const c of read?.columns ?? []) state[c.column_id] = toChoice(c.mask_rule)
  return state
}

function toChoice(rule: MaskRule | null | undefined): MaskChoice {
  if (!rule) return 'none'
  return rule.kind === 'phone' || rule.kind === 'id_card' || rule.kind === 'full' ? rule.kind : 'full'
}

export function grantPayload(state: GrantState): ClientColumnWrite[] {
  return Object.entries(state)
    .map(([id, mask]) => ({ column_id: Number(id), mask_rule: mask === 'none' ? null : { kind: mask, prefix: 0, suffix: 0 } }))
    .sort((a, b) => a.column_id - b.column_id)
}

export function tableState(table: TableRead, state: GrantState): CheckState {
  const cols = table.columns ?? []
  const granted = cols.filter((c) => c.id in state).length
  return granted === 0 ? 'off' : granted === cols.length ? 'on' : 'some'
}

/** 勾选整表：全部字段授权（已设的脱敏保留）；取消：整表撤销。 */
export function setTable(state: GrantState, table: TableRead, on: boolean): GrantState {
  const next = { ...state }
  for (const c of table.columns ?? []) {
    if (on) next[c.id] = next[c.id] ?? 'none'
    else delete next[c.id]
  }
  return next
}

export function setColumn(state: GrantState, columnId: number, on: boolean): GrantState {
  const next = { ...state }
  if (on) next[columnId] = next[columnId] ?? 'none'
  else delete next[columnId]
  return next
}

export function sameGrants(a: GrantState, b: GrantState): boolean {
  const ka = Object.keys(a)
  return ka.length === Object.keys(b).length && ka.every((k) => a[Number(k)] === b[Number(k)])
}

export type ClientStatus = 'active' | 'disabled' | 'expired'

export function clientStatus(c: Pick<McpClientRead, 'status' | 'expires_at'>, now = Date.now()): ClientStatus {
  if (c.status !== 'active') return 'disabled'
  if (c.expires_at && Date.parse(c.expires_at) <= now) return 'expired'
  return 'active'
}

export const STATUS_LABEL: Record<ClientStatus, string> = { active: '启用', disabled: '已停用', expired: '已过期' }

/** 仍可用的密钥：未吊销，且未过宽限期。 */
export function liveKeys(c: Pick<McpClientRead, 'keys'>, now = Date.now()) {
  return (c.keys ?? []).filter((k) => !k.revoked_at && !(k.expires_at && Date.parse(k.expires_at) <= now))
}

export const KEY_PLACEHOLDER = '<你的 API Key>'

/** 三种客户端的接入配置。key 只在刚创建或轮换后可见，其余时候用占位符。 */
export function snippets(url: string, key: string | null): { id: string; label: string; text: string }[] {
  const secret = key ?? KEY_PLACEHOLDER
  const server = { type: 'http', url, headers: { Authorization: `Bearer ${secret}` } }
  return [
    { id: 'claude', label: 'Claude Code', text: `claude mcp add --transport http askdata ${url} --header "Authorization: Bearer ${secret}"` },
    { id: 'cursor', label: 'Cursor', text: JSON.stringify({ mcpServers: { askdata: { url, headers: server.headers } } }, null, 2) },
    { id: 'codex', label: 'Codex', text: `# Key 放在环境变量里（写进 ~/.zshrc 等），Codex 启动时读取\nexport ASKDATA_MCP_KEY="${secret}"\ncodex mcp add askdata --url ${url} --bearer-token-env-var ASKDATA_MCP_KEY` },
    { id: 'json', label: '通用 JSON', text: JSON.stringify({ mcpServers: { askdata: server } }, null, 2) },
  ]
}
