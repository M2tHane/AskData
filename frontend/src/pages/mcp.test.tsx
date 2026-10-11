import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../components/Toast'
import { navFor } from '../lib/nav'
import { McpPage } from './McpPage'

const KEY = 'ak_test_full_key_shown_once_0123456789'

function client(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    public_id: 'mc_alpha',
    name: '销售分析 Agent',
    status: 'active',
    expires_at: null,
    daily_call_limit: 2000,
    default_rows: 100,
    max_rows: 1000,
    ask_timeout_seconds: 60,
    allow_run_sql: false,
    calls_today: 0,
    last_used_at: null,
    created_at: '2026-10-01T00:00:00Z',
    datasource_ids: [],
    override_count: 0,
    stale_grants: 0,
    keys: [{ id: 1, prefix: KEY.slice(0, 10), created_at: '2026-10-01T00:00:00Z', expires_at: null, revoked_at: null, last_used_at: null }],
    ...over,
  }
}

/** A tiny in-memory API: enough of /api for the page, recording what it was sent. */
function serve(state: { clients: ReturnType<typeof client>[]; items?: unknown[]; grants?: unknown[] }) {
  const sent: { method: string; path: string; body: unknown }[] = []
  const reply = (data: unknown, status = 200) => new Response(JSON.stringify({ code: 'OK', message: 'ok', data }), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      sent.push({ method, path: url.pathname, body })
      const path = url.pathname
      if (path === '/api/mcp-clients' && method === 'GET') return reply(state.clients)
      if (path === '/api/mcp-clients' && method === 'POST') {
        state.clients = [client({ name: body.name })]
        return reply({ key: KEY, client_id: 1, detail: state.clients[0]?.keys[0] }, 201)
      }
      if (path === '/api/mcp-clients/1') return reply(state.clients[0])
      if (path === '/api/datasources/available') return reply({ items: [{ id: 7, name: 'shop', type: 'postgresql' }], total: 1, page: 1, page_size: 100 })
      if (path === '/api/schema/7/tables')
        return reply([
          {
            id: 3,
            name: 'orders',
            qualified_name: 'public.orders',
            columns: [
              { id: 11, name: 'id', data_type: 'bigint' },
              { id: 12, name: 'phone', data_type: 'text' },
            ],
          },
        ])
      if (path === '/api/mcp-clients/1/grants/7' && method === 'GET') return reply({ datasource_id: 7, columns: state.grants ?? [] })
      if (path === '/api/mcp-clients/1/grants/7' && method === 'PUT') return reply({ datasource_id: 7, columns: body.columns })
      if (path === '/api/mcp-clients/1/items') return reply(state.items ?? [])
      return reply(null, 404)
    }),
  )
  return sent
}

function mount() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <ToastProvider>
        <McpPage />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn(async () => undefined) }, configurable: true })
})
afterEach(() => vi.unstubAllGlobals())

describe('MCP page', () => {
  it('shows a new key exactly once, then opens the connection on its data grants', async () => {
    serve({ clients: [] })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: '新建接入' }))
    await userEvent.type(screen.getByLabelText('名称'), '销售分析 Agent')
    await userEvent.click(screen.getByRole('button', { name: '创建接入' }))
    const dialog = await screen.findByRole('dialog', { name: '接入已创建' })
    expect(within(dialog).getByText(KEY)).toBeInTheDocument()
    expect(within(dialog).getByText(/关闭后不再显示/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '我已保存' }))
    await waitFor(() => expect(screen.queryByText(KEY)).toBeNull())
    // The detail opens where a new connection needs work first: nothing is granted yet.
    expect(await screen.findByRole('tab', { name: /数据权限/, selected: true })).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(KEY)
  })

  it('saves exactly the checked columns with their masks', async () => {
    const sent = serve({ clients: [client()] })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: '管理' }))
    await userEvent.click(await screen.findByRole('tab', { name: /数据权限/ }))
    await userEvent.click(await screen.findByRole('button', { name: /orders/ }))
    await userEvent.click(await screen.findByLabelText('可查字段 orders.phone'))
    await userEvent.click(screen.getByRole('combobox', { name: 'orders.phone 脱敏方式' }))
    await userEvent.click(screen.getByRole('option', { name: '手机号' }))
    expect(screen.getByText('有未保存的修改')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(sent.some((r) => r.method === 'PUT')).toBe(true))
    const put = sent.find((r) => r.method === 'PUT')
    expect(put?.path).toBe('/api/mcp-clients/1/grants/7')
    expect(put?.body).toEqual({ columns: [{ column_id: 12, mask_rule: { kind: 'phone', prefix: 0, suffix: 0 } }] })
  })

  it('says when edits are waiting for a grant instead of hiding them', async () => {
    serve({
      clients: [client({ override_count: 3 })],
      items: [
        { kind: 'metric', item_id: 1, name: '销售额', state: 'edited', platform: { description: '实付合计' }, effective: { description: '含运费' } },
        { kind: 'semantic', item_id: 2, name: '实付金额', state: 'default', platform: {}, effective: { description: '订单实付' } },
      ],
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: '管理' }))
    await userEvent.click(await screen.findByRole('tab', { name: /语义与指标/ }))
    expect(await screen.findByText('含运费')).toBeInTheDocument()
    expect(screen.getByText(/1 项已修改或隐藏/)).toHaveTextContent('另有 2 项修改不在此列')
    expect(screen.getByRole('button', { name: '全部恢复默认' })).toBeEnabled()
  })

  it('is an admin-only entry', () => {
    expect(navFor('admin').routes.map((r) => r.id)).toContain('mcp')
    for (const role of ['leader', 'member'] as const) expect(navFor(role).routes.map((r) => r.id)).not.toContain('mcp')
  })
})
