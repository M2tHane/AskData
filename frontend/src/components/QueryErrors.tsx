import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, describeError } from '../lib/errors'
import { useToast } from './Toast'

/** 同一个查询在这段时间内重复失败（例如轮询）只提示一次。 */
const QUIET_MS = 30_000

/**
 * 任何页面的数据加载失败都弹出提示，不再显示成「空列表」让人误以为没有数据。
 * 个别本来就可能失败、页面自己会处理的查询，用 meta: { silent: true } 跳过。
 */
export function QueryErrors() {
  const cache = useQueryClient().getQueryCache()
  const toast = useToast()
  useEffect(() => {
    const shown = new Map<string, number>()
    return cache.subscribe((event) => {
      if (event.type !== 'updated' || event.action.type !== 'error') return
      const query = event.query
      if (query.meta?.silent) return
      const error = event.action.error
      // 登录失效由全局登出处理，不重复提示。
      if (error instanceof ApiError && error.status === 401) return
      const now = Date.now()
      if (now - (shown.get(query.queryHash) ?? 0) < QUIET_MS) return
      shown.set(query.queryHash, now)
      toast({ tone: 'danger', title: '数据加载失败', message: error instanceof ApiError ? describeError(error.code) : '网络异常，请稍后重试。' })
    })
  }, [cache, toast])
  return null
}
