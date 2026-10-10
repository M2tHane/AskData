import { ApiError } from '../lib/errors'
import { useAuth } from '../state/auth'
import type { StreamEvent, TokenPair } from './types'

interface Envelope<T> {
  code: string
  message: string
  data: T | null
}

let refreshing: Promise<boolean> | null = null

async function refreshTokens(): Promise<boolean> {
  const { refresh, setTokens, clear } = useAuth.getState()
  if (!refresh) return false
  refreshing ??= (async () => {
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      const body = (await res.json()) as Envelope<TokenPair>
      if (!res.ok || !body.data) {
        clear()
        return false
      }
      setTokens(body.data)
      return true
    } catch {
      clear()
      return false
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

function headers(json: boolean): Record<string, string> {
  const h: Record<string, string> = {}
  const { access } = useAuth.getState()
  if (access) h.Authorization = `Bearer ${access}`
  if (json) h['Content-Type'] = 'application/json'
  return h
}

async function send(path: string, init: RequestInit, retry = true): Promise<Response> {
  const res = await fetch(path, init)
  if (res.status === 401 && retry && !path.startsWith('/api/auth/')) {
    if (await refreshTokens()) {
      return send(path, { ...init, headers: { ...(init.headers as Record<string, string>), ...headers(false) } }, false)
    }
  }
  return res
}

export async function api<T>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  options: { body?: unknown; query?: Record<string, string | number | boolean | null | undefined | (string | number)[]> } = {},
): Promise<T> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(options.query ?? {})) {
    // Arrays repeat the key (?id=1&id=2), which is how FastAPI reads list parameters.
    if (Array.isArray(v)) for (const item of v) qs.append(k, String(item))
    else if (v != null && v !== '') qs.set(k, String(v))
  }
  const url = qs.size ? `${path}?${qs}` : path
  const res = await send(url, {
    method,
    headers: headers(options.body !== undefined),
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  let body: Envelope<T> | null = null
  try {
    body = (await res.json()) as Envelope<T>
  } catch {
    /* non-JSON failure falls through to a generic error */
  }
  if (!res.ok || !body || body.code !== 'OK') {
    if (res.status === 401) useAuth.getState().clear()
    throw new ApiError(body?.code ?? 'INTERNAL_ERROR', res.status)
  }
  return body.data as T
}

/** Download a file; exports report how many rows they hold and whether a cap cut them. */
export async function download(path: string, filename: string): Promise<{ rows: number | null; truncated: boolean }> {
  const res = await send(path, { headers: headers(false) })
  if (!res.ok) throw new ApiError('INTERNAL_ERROR', res.status)
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
  const rows = res.headers.get('X-Export-Rows')
  return { rows: rows == null ? null : Number(rows), truncated: res.headers.get('X-Export-Truncated') === '1' }
}

/** Parse a `text/event-stream` body; payloads are JSON objects, one per event. */
export function parseSse(buffer: string): { events: StreamEvent[]; rest: string } {
  const events: StreamEvent[] = []
  const blocks = buffer.split('\n\n')
  const rest = blocks.pop() ?? ''
  for (const block of blocks) {
    let event = ''
    let data = ''
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7)
      else if (line.startsWith('data: ')) data += line.slice(6)
    }
    if (!event || !data) continue
    try {
      events.push({ event, data: JSON.parse(data) } as StreamEvent)
    } catch {
      /* ignore a malformed frame rather than breaking the stream */
    }
  }
  return { events, rest }
}

export async function streamQuestion(
  conversationId: number,
  payload: { question: string; clarification_message_id?: number; clarification_choice?: string; referenced_metric_ids?: number[]; model_id?: number; timezone: string },
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await send(`/api/chat/conversations/${conversationId}/messages/stream`, {
    method: 'POST',
    headers: headers(true),
    body: JSON.stringify(payload),
    signal,
  })
  if (!res.ok || !res.body) {
    let code = 'INTERNAL_ERROR'
    try {
      code = ((await res.json()) as Envelope<unknown>).code
    } catch {
      /* keep generic code */
    }
    throw new ApiError(code, res.status)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replaceAll('\r\n', '\n')
    const parsed = parseSse(buffer)
    buffer = parsed.rest
    parsed.events.forEach(onEvent)
  }
}
