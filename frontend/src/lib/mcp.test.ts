import { describe, expect, it } from 'vitest'
import type { TableRead } from '../api/types'
import { clientStatus, grantPayload, grantState, KEY_PLACEHOLDER, liveKeys, sameGrants, setColumn, setTable, snippets, tableState } from './mcp'

const table = { id: 1, name: 'orders', columns: [{ id: 11 }, { id: 12 }, { id: 13 }] } as unknown as TableRead

describe('MCP 接入的可查范围', () => {
  it('读出授权和脱敏，写回同样的形状', () => {
    const state = grantState({ datasource_id: 1, client_id: 2, columns: [{ column_id: 12, mask_rule: { kind: 'phone', prefix: 0, suffix: 0 } }, { column_id: 11, mask_rule: null }] })
    expect(state).toEqual({ 11: 'none', 12: 'phone' })
    expect(grantPayload(state)).toEqual([
      { column_id: 11, mask_rule: null },
      { column_id: 12, mask_rule: { kind: 'phone', prefix: 0, suffix: 0 } },
    ])
  })

  it('整表勾选保留已设的脱敏，取消则整表撤销', () => {
    const some = setColumn({}, 12, true)
    expect(tableState(table, some)).toBe('some')
    const all = setTable({ 12: 'full' }, table, true)
    expect(all).toEqual({ 11: 'none', 12: 'full', 13: 'none' })
    expect(tableState(table, all)).toBe('on')
    expect(setTable(all, table, false)).toEqual({})
    expect(sameGrants(all, { ...all })).toBe(true)
    expect(sameGrants(all, { ...all, 12: 'none' })).toBe(false)
  })
})

describe('MCP 接入状态与密钥', () => {
  const now = Date.parse('2026-10-11T00:00:00Z')
  it('停用优先，其次看是否过期', () => {
    expect(clientStatus({ status: 'disabled', expires_at: null }, now)).toBe('disabled')
    expect(clientStatus({ status: 'active', expires_at: '2026-10-10T00:00:00Z' }, now)).toBe('expired')
    expect(clientStatus({ status: 'active', expires_at: null }, now)).toBe('active')
  })

  it('吊销或过了宽限期的 Key 不算可用', () => {
    const keys = [
      { id: 1, prefix: 'a', created_at: '', revoked_at: '2026-10-01T00:00:00Z', expires_at: null, last_used_at: null },
      { id: 2, prefix: 'b', created_at: '', revoked_at: null, expires_at: '2026-10-10T00:00:00Z', last_used_at: null },
      { id: 3, prefix: 'c', created_at: '', revoked_at: null, expires_at: '2026-10-12T00:00:00Z', last_used_at: null },
      { id: 4, prefix: 'd', created_at: '', revoked_at: null, expires_at: null, last_used_at: null },
    ]
    expect(liveKeys({ keys }, now).map((k) => k.id)).toEqual([3, 4])
  })

  it('配置片段只在刚拿到 Key 时带真实值', () => {
    const withKey = snippets('http://x/mcp', 'ak_secret')
    expect(withKey[0]?.text).toContain('--transport http askdata http://x/mcp')
    expect(withKey.every((s) => s.text.includes('Bearer ak_secret'))).toBe(true)
    expect(snippets('http://x/mcp', null).every((s) => s.text.includes(KEY_PLACEHOLDER))).toBe(true)
    expect(JSON.parse(withKey[2]?.text ?? '{}').mcpServers.askdata.type).toBe('http')
  })
})
